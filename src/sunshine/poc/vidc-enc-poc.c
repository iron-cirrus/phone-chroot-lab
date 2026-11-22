/*
 * vidc-enc-poc : preuve de concept d'encodage H.264 materiel sur l'encodeur
 * Qualcomm msm_vidc (/dev/video33) depuis un chroot Linux, via l'API V4L2
 * mem2mem multiplan, avec des tampons d'entree DMABUF alloues dans /dev/dma_heap
 * (le pilote Android refuse les tampons MMAP qu'utilise FFmpeg).
 *
 * Usage : vidc-enc-poc <largeur> <hauteur> <nb_images> <sortie.h264> <reference.nv12> [heap]
 * Genere une mire animee, l'encode, et ecrit aussi la mire brute (NV12 compact)
 * pour comparer ensuite (PSNR) le flux decode a la source.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>
#include <linux/videodev2.h>
#include <linux/dma-heap.h>

#define NOUT 4   /* nombre demande ; le pilote peut en imposer davantage */
#define NCAP 4
#define MAXB 32  /* taille des tableaux : couvre ce que le pilote peut imposer */
#define DIE(...) do { fprintf(stderr, "ERREUR: " __VA_ARGS__); fprintf(stderr, " (errno %d: %s)\n", errno, strerror(errno)); exit(1); } while (0)

static int xioctl(int fd, unsigned long req, void *arg)
{
    int r;
    do { r = ioctl(fd, req, arg); } while (r < 0 && errno == EINTR);
    return r;
}

static double now(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
}

static int dmabuf_alloc(int heap, size_t len)
{
    struct dma_heap_allocation_data a;
    memset(&a, 0, sizeof a);
    a.len = len;
    a.fd_flags = O_RDWR | O_CLOEXEC;
    if (xioctl(heap, DMA_HEAP_IOCTL_ALLOC, &a) < 0)
        return -1;
    return (int)a.fd;
}

/* mire : degrade horizontal + barre verticale qui se deplace + damier en chroma */
static void pattern(uint8_t *y, uint8_t *uv, int w, int h, int ystride, int uvstride, int n)
{
    int bar = (n * 8) % w;
    for (int j = 0; j < h; j++)
        for (int i = 0; i < w; i++) {
            int v = (i * 255) / w;
            if (i >= bar && i < bar + w / 16) v = 235;
            if (((j + n * 2) / 32) % 2 == 0 && i < w / 8) v = 16;
            y[j * ystride + i] = (uint8_t)v;
        }
    for (int j = 0; j < h / 2; j++)
        for (int i = 0; i < w / 2; i++) {
            uv[j * uvstride + 2 * i]     = (uint8_t)(64 + ((i / 16 + j / 16) % 2) * 128);
            uv[j * uvstride + 2 * i + 1] = (uint8_t)(128 + ((j * 2 + n) % 64) - 32);
        }
}

static void set_ctrl(int fd, uint32_t id, int32_t val, const char *nom)
{
    struct v4l2_control c = { .id = id, .value = val };
    if (xioctl(fd, VIDIOC_S_CTRL, &c) < 0)
        fprintf(stderr, "  (controle %s refuse : %s)\n", nom, strerror(errno));
}

int main(int argc, char **argv)
{
    if (argc < 6) {
        fprintf(stderr, "usage: %s largeur hauteur images sortie.h264 reference.nv12 [heap]\n", argv[0]);
        return 2;
    }
    int W = atoi(argv[1]), H = atoi(argv[2]), N = atoi(argv[3]);
    const char *heapname = argc > 6 ? argv[6] : "/dev/dma_heap/system";
    FILE *out = fopen(argv[4], "wb"), *ref = fopen(argv[5], "wb");
    if (!out || !ref) DIE("ouverture des fichiers de sortie");

    int fd = open("/dev/video33", O_RDWR | O_NONBLOCK);
    if (fd < 0) DIE("ouverture /dev/video33");
    int heap = open(heapname, O_RDWR | O_CLOEXEC);
    if (heap < 0) DIE("ouverture %s", heapname);

    struct v4l2_capability cap;
    if (xioctl(fd, VIDIOC_QUERYCAP, &cap) < 0) DIE("QUERYCAP");
    printf("peripherique : %s / %s\n", cap.driver, cap.card);

    /* 1) format de sortie compressee (CAPTURE) d'abord, comme le demande l'API encodeur */
    struct v4l2_format fc;
    memset(&fc, 0, sizeof fc);
    fc.type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE;
    fc.fmt.pix_mp.width = W;
    fc.fmt.pix_mp.height = H;
    fc.fmt.pix_mp.pixelformat = V4L2_PIX_FMT_H264;
    fc.fmt.pix_mp.num_planes = 1;
    fc.fmt.pix_mp.plane_fmt[0].sizeimage = W * H; /* largement suffisant pour une image compressee */
    if (xioctl(fd, VIDIOC_S_FMT, &fc) < 0) DIE("S_FMT capture H264");

    /* 2) format d'entree brute (OUTPUT) : NV12 */
    struct v4l2_format fo;
    memset(&fo, 0, sizeof fo);
    fo.type = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE;
    fo.fmt.pix_mp.width = W;
    fo.fmt.pix_mp.height = H;
    fo.fmt.pix_mp.pixelformat = V4L2_PIX_FMT_NV12;
    fo.fmt.pix_mp.num_planes = 1;
    if (xioctl(fd, VIDIOC_S_FMT, &fo) < 0) DIE("S_FMT output NV12");
    int stride = fo.fmt.pix_mp.plane_fmt[0].bytesperline;
    size_t insize = fo.fmt.pix_mp.plane_fmt[0].sizeimage;
    printf("entree NV12 : %ux%u, plans=%u, pas=%d, taille=%zu\n", fo.fmt.pix_mp.width, fo.fmt.pix_mp.height,
           fo.fmt.pix_mp.num_planes, stride, insize);
    if (fo.fmt.pix_mp.num_planes != 1) DIE("NV12 en %u plans non gere par ce PoC", fo.fmt.pix_mp.num_planes);
    /* hauteur de plan Y alignee (Venus aligne sur 32 lignes) : deduite de la taille annoncee */
    int scan = H;
    for (int a = H; a <= H + 64; a++)
        if ((size_t)stride * a * 3 / 2 <= insize) scan = a;
    int s32 = (H + 31) & ~31;
    if ((size_t)stride * s32 * 3 / 2 <= insize) scan = s32;
    printf("lignes du plan Y retenues : %d (UV a l'octet %d)\n", scan, stride * scan);
    if (xioctl(fd, VIDIOC_G_FMT, &fc) == 0)
        printf("sortie H264 : taille de tampon=%u\n", fc.fmt.pix_mp.plane_fmt[0].sizeimage);

    /* parametres : 60 im/s, 8 Mbit/s, image cle toutes les 60 images */
    struct v4l2_streamparm sp;
    memset(&sp, 0, sizeof sp);
    sp.type = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE;
    sp.parm.output.timeperframe.numerator = 1;
    sp.parm.output.timeperframe.denominator = 60;
    if (xioctl(fd, VIDIOC_S_PARM, &sp) < 0) fprintf(stderr, "  (S_PARM refuse : %s)\n", strerror(errno));
    set_ctrl(fd, V4L2_CID_MPEG_VIDEO_BITRATE, 8000000, "debit");
    set_ctrl(fd, V4L2_CID_MPEG_VIDEO_GOP_SIZE, 60, "GOP");
    set_ctrl(fd, V4L2_CID_MPEG_VIDEO_H264_PROFILE, V4L2_MPEG_VIDEO_H264_PROFILE_HIGH, "profil");
    set_ctrl(fd, V4L2_CID_MPEG_VIDEO_PREPEND_SPSPPS_TO_IDR, 1, "SPS/PPS avant IDR");

    /* 3) tampons : entree en DMABUF, sortie en MMAP (on tente) */
    struct v4l2_requestbuffers rb;
    memset(&rb, 0, sizeof rb);
    rb.count = NOUT; rb.type = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE; rb.memory = V4L2_MEMORY_DMABUF;
    if (xioctl(fd, VIDIOC_REQBUFS, &rb) < 0) DIE("REQBUFS output DMABUF");
    int nout = rb.count;
    if (nout > MAXB) DIE("le pilote impose %d tampons d'entree (max %d)", nout, MAXB);
    int infd[MAXB]; uint8_t *inmap[MAXB];
    for (int i = 0; i < nout; i++) {
        infd[i] = dmabuf_alloc(heap, insize);
        if (infd[i] < 0) DIE("allocation DMABUF de %zu octets dans %s", insize, heapname);
        inmap[i] = mmap(NULL, insize, PROT_READ | PROT_WRITE, MAP_SHARED, infd[i], 0);
        if (inmap[i] == MAP_FAILED) DIE("mmap DMABUF");
    }
    /* sortie compressee : DMABUF aussi (le pilote ne sait pas projeter ses propres tampons MMAP) */
    memset(&rb, 0, sizeof rb);
    rb.count = NCAP; rb.type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE; rb.memory = V4L2_MEMORY_DMABUF;
    int capdmabuf = 1;
    if (xioctl(fd, VIDIOC_REQBUFS, &rb) < 0) {
        fprintf(stderr, "  (sortie en DMABUF refusee : %s ; essai en MMAP)\n", strerror(errno));
        memset(&rb, 0, sizeof rb);
        rb.count = NCAP; rb.type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE; rb.memory = V4L2_MEMORY_MMAP;
        if (xioctl(fd, VIDIOC_REQBUFS, &rb) < 0) DIE("REQBUFS capture");
        capdmabuf = 0;
    }
    int ncap = rb.count;
    if (ncap > MAXB) DIE("le pilote impose %d tampons de sortie (max %d)", ncap, MAXB);
    size_t capsize = fc.fmt.pix_mp.plane_fmt[0].sizeimage;
    uint8_t *capmap[MAXB]; size_t caplen[MAXB]; int capfd[MAXB];
    for (int i = 0; i < ncap; i++) {
        struct v4l2_plane pl; struct v4l2_buffer b;
        memset(&pl, 0, sizeof pl); memset(&b, 0, sizeof b);
        b.type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE; b.index = i; b.length = 1; b.m.planes = &pl;
        if (capdmabuf) {
            b.memory = V4L2_MEMORY_DMABUF;
            capfd[i] = dmabuf_alloc(heap, capsize);
            if (capfd[i] < 0) DIE("allocation DMABUF sortie");
            capmap[i] = mmap(NULL, capsize, PROT_READ, MAP_SHARED, capfd[i], 0);
            caplen[i] = capsize;
            pl.m.fd = capfd[i]; pl.length = capsize;
        } else {
            b.memory = V4L2_MEMORY_MMAP;
            if (xioctl(fd, VIDIOC_QUERYBUF, &b) < 0) DIE("QUERYBUF capture");
            caplen[i] = pl.length;
            capmap[i] = mmap(NULL, pl.length, PROT_READ, MAP_SHARED, fd, pl.m.mem_offset);
            if (capmap[i] == MAP_FAILED) DIE("mmap capture");
        }
        if (xioctl(fd, VIDIOC_QBUF, &b) < 0) DIE("QBUF capture %d", i);
    }
    printf("tampons : %d entree (DMABUF %s), %d sortie (%s)\n", nout, heapname, ncap, capdmabuf ? "DMABUF" : "MMAP");

    int t = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE;
    if (xioctl(fd, VIDIOC_STREAMON, &t) < 0) DIE("STREAMON output");
    t = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE;
    if (xioctl(fd, VIDIOC_STREAMON, &t) < 0) DIE("STREAMON capture");

    uint8_t *refbuf = malloc((size_t)W * H * 3 / 2);
    int sent = 0, got = 0, freeout[MAXB], nfree = nout;
    for (int i = 0; i < nout; i++) freeout[i] = i;
    size_t total = 0;
    struct rusage r0, r1; getrusage(RUSAGE_SELF, &r0);
    double t0 = now(), tlast = t0;
    while (got < N) {
        /* envoyer des images tant qu'un tampon d'entree est libre */
        while (sent < N && nfree > 0) {
            int i = freeout[--nfree];
            /* POC_STATIC=1 : chaque tampon n'est dessine qu'une fois, pour mesurer le cout de l'encodage seul */
            static int drawn[MAXB];
            if (!getenv("POC_STATIC") || !drawn[i]) {
                pattern(inmap[i], inmap[i] + (size_t)stride * scan, W, H, stride, stride, sent);
                drawn[i] = 1;
            }
            if (!getenv("POC_STATIC")) {
                pattern(refbuf, refbuf + (size_t)W * H, W, H, W, W, sent);
                fwrite(refbuf, 1, (size_t)W * H * 3 / 2, ref);
            }
            struct v4l2_plane pl; struct v4l2_buffer b;
            memset(&pl, 0, sizeof pl); memset(&b, 0, sizeof b);
            b.type = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE; b.memory = V4L2_MEMORY_DMABUF; b.index = i;
            b.length = 1; b.m.planes = &pl;
            pl.m.fd = infd[i]; pl.length = insize; pl.bytesused = insize;
            b.timestamp.tv_sec = sent / 60; b.timestamp.tv_usec = (sent % 60) * 1000000 / 60;
            if (xioctl(fd, VIDIOC_QBUF, &b) < 0) DIE("QBUF output image %d", sent);
            sent++;
        }
        struct pollfd p = { .fd = fd, .events = POLLIN | POLLOUT };
        if (poll(&p, 1, 3000) <= 0) DIE("delai depasse en attendant l'encodeur (envoyees %d, recues %d)", sent, got);
        /* recuperer les tampons d'entree consommes */
        for (;;) {
            struct v4l2_plane pl; struct v4l2_buffer b;
            memset(&pl, 0, sizeof pl); memset(&b, 0, sizeof b);
            b.type = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE; b.memory = V4L2_MEMORY_DMABUF; b.length = 1; b.m.planes = &pl;
            if (xioctl(fd, VIDIOC_DQBUF, &b) < 0) break;
            freeout[nfree++] = b.index;
        }
        /* recuperer les images compressees */
        for (;;) {
            struct v4l2_plane pl; struct v4l2_buffer b;
            memset(&pl, 0, sizeof pl); memset(&b, 0, sizeof b);
            b.type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE; b.memory = capdmabuf ? V4L2_MEMORY_DMABUF : V4L2_MEMORY_MMAP;
            b.length = 1; b.m.planes = &pl;
            if (xioctl(fd, VIDIOC_DQBUF, &b) < 0) break;
            size_t n = pl.bytesused > pl.data_offset ? pl.bytesused - pl.data_offset : 0;
            if (n) { fwrite(capmap[b.index] + pl.data_offset, 1, n, out); total += n; got++; }
            if (got == 1) printf("premiere image compressee : %zu octets apres %.1f ms\n", n, (now() - t0) * 1000);
            if (capdmabuf) pl.m.fd = capfd[b.index];
            pl.bytesused = 0; pl.length = caplen[b.index];
            if (xioctl(fd, VIDIOC_QBUF, &b) < 0) DIE("re-QBUF capture");
        }
        tlast = now();
    }
    getrusage(RUSAGE_SELF, &r1);
    double el = tlast - t0;
    double cpu = (r1.ru_utime.tv_sec - r0.ru_utime.tv_sec) + (r1.ru_utime.tv_usec - r0.ru_utime.tv_usec) / 1e6
               + (r1.ru_stime.tv_sec - r0.ru_stime.tv_sec) + (r1.ru_stime.tv_usec - r0.ru_stime.tv_usec) / 1e6;
    printf("RESULTAT : %d images %dx%d en %.2f s = %.1f im/s | %.1f Mo produits (%.1f Mbit/s a 60 im/s)\n",
           got, W, H, el, got / el, total / 1e6, total * 8.0 / 1e6 / (got / 60.0));
    printf("CPU du programme (generation de la mire comprise) : %.2f s, soit %.0f %% d'un coeur\n", cpu, 100 * cpu / el);
    fclose(out); fclose(ref);
    t = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE; xioctl(fd, VIDIOC_STREAMOFF, &t);
    t = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE; xioctl(fd, VIDIOC_STREAMOFF, &t);
    return 0;
}
