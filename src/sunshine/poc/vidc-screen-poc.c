/*
 * vidc-screen-poc : chaine complete capture d'ecran -> conversion -> encodage materiel
 *   1. capture de l'ecran X (XShmGetImage, memoire partagee emulee par libsysvshm-emu)
 *   2. conversion BGRX -> NV12 avec libyuv, ecrite directement dans le tampon DMABUF
 *   3. encodage H.264 par l'encodeur Qualcomm msm_vidc (/dev/video33, V4L2 mem2mem)
 * Rythme impose (images/s cible) ; mesure le temps de chaque etape et le CPU total.
 *
 * Usage : vidc-screen-poc <largeur> <hauteur> <im/s> <duree_s> <sortie.h264|/dev/null>
 * (la largeur/hauteur doivent etre celles de l'ecran X capture)
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
#include <sys/ipc.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <sys/shm.h>
#include <time.h>
#include <unistd.h>
#include <linux/videodev2.h>
#include <linux/dma-heap.h>
#include <linux/dma-buf.h>
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <X11/extensions/XShm.h>
#include <libyuv.h>

#define MAXB 32
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
    return xioctl(heap, DMA_HEAP_IOCTL_ALLOC, &a) < 0 ? -1 : (int)a.fd;
}

static void dmabuf_sync(int fd, uint64_t flags)
{
    struct dma_buf_sync s = { .flags = flags };
    xioctl(fd, DMA_BUF_IOCTL_SYNC, &s);
}

static double cpu_s(void)
{
    struct rusage r;
    getrusage(RUSAGE_SELF, &r);
    return r.ru_utime.tv_sec + r.ru_utime.tv_usec / 1e6 + r.ru_stime.tv_sec + r.ru_stime.tv_usec / 1e6;
}

int main(int argc, char **argv)
{
    if (argc < 6) {
        fprintf(stderr, "usage: %s largeur hauteur im/s duree_s sortie.h264\n", argv[0]);
        return 2;
    }
    int W = atoi(argv[1]), H = atoi(argv[2]), FPS = atoi(argv[3]);
    double DUR = atof(argv[4]);
    FILE *out = fopen(argv[5], "wb");
    if (!out) DIE("ouverture sortie");

    /* --- capture X (XShm) --- */
    Display *dpy = XOpenDisplay(NULL);
    if (!dpy) DIE("ouverture affichage X");
    Window root = DefaultRootWindow(dpy);
    XWindowAttributes wa;
    XGetWindowAttributes(dpy, root, &wa);
    if (wa.width != W || wa.height != H) DIE("ecran X %dx%d different de %dx%d", wa.width, wa.height, W, H);
    if (!XShmQueryExtension(dpy)) DIE("XShm indisponible (LD_PRELOAD de libsysvshm-emu ?)");
    XShmSegmentInfo si;
    XImage *img = XShmCreateImage(dpy, wa.visual, wa.depth, ZPixmap, NULL, &si, W, H);
    si.shmid = shmget(IPC_PRIVATE, img->bytes_per_line * img->height, IPC_CREAT | 0600);
    if (si.shmid < 0) DIE("shmget");
    si.shmaddr = img->data = shmat(si.shmid, NULL, 0);
    si.readOnly = False;
    XShmAttach(dpy, &si);
    XSync(dpy, False);
    shmctl(si.shmid, IPC_RMID, NULL);
    printf("capture : ecran %dx%d, %d bits/pixel, pas %d\n", W, H, img->bits_per_pixel, img->bytes_per_line);

    /* --- encodeur --- */
    int fd = open("/dev/video33", O_RDWR | O_NONBLOCK);
    if (fd < 0) DIE("ouverture /dev/video33");
    int heap = open("/dev/dma_heap/system", O_RDWR | O_CLOEXEC);
    if (heap < 0) DIE("ouverture dma_heap");
    struct v4l2_format fc, fo;
    memset(&fc, 0, sizeof fc);
    fc.type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE;
    fc.fmt.pix_mp.width = W; fc.fmt.pix_mp.height = H;
    fc.fmt.pix_mp.pixelformat = V4L2_PIX_FMT_H264; fc.fmt.pix_mp.num_planes = 1;
    fc.fmt.pix_mp.plane_fmt[0].sizeimage = W * H;
    if (xioctl(fd, VIDIOC_S_FMT, &fc) < 0) DIE("S_FMT H264");
    memset(&fo, 0, sizeof fo);
    fo.type = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE;
    fo.fmt.pix_mp.width = W; fo.fmt.pix_mp.height = H;
    fo.fmt.pix_mp.pixelformat = V4L2_PIX_FMT_NV12; fo.fmt.pix_mp.num_planes = 1;
    if (xioctl(fd, VIDIOC_S_FMT, &fo) < 0) DIE("S_FMT NV12");
    int stride = fo.fmt.pix_mp.plane_fmt[0].bytesperline;
    size_t insize = fo.fmt.pix_mp.plane_fmt[0].sizeimage;
    int scan = H, s32 = (H + 31) & ~31;
    if ((size_t)stride * s32 * 3 / 2 <= insize) scan = s32;
    xioctl(fd, VIDIOC_G_FMT, &fc);
    size_t capsize = fc.fmt.pix_mp.plane_fmt[0].sizeimage;
    printf("encodeur : NV12 pas %d, %d lignes Y, tampon %zu ; sortie %zu\n", stride, scan, insize, capsize);

    struct v4l2_streamparm sp;
    memset(&sp, 0, sizeof sp);
    sp.type = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE;
    sp.parm.output.timeperframe.numerator = 1;
    sp.parm.output.timeperframe.denominator = FPS;
    xioctl(fd, VIDIOC_S_PARM, &sp);
    struct v4l2_control c;
    c.id = V4L2_CID_MPEG_VIDEO_BITRATE; c.value = 20000000; xioctl(fd, VIDIOC_S_CTRL, &c);
    c.id = V4L2_CID_MPEG_VIDEO_GOP_SIZE; c.value = FPS * 2; xioctl(fd, VIDIOC_S_CTRL, &c);
    c.id = V4L2_CID_MPEG_VIDEO_H264_PROFILE; c.value = V4L2_MPEG_VIDEO_H264_PROFILE_HIGH; xioctl(fd, VIDIOC_S_CTRL, &c);
    c.id = V4L2_CID_MPEG_VIDEO_PREPEND_SPSPPS_TO_IDR; c.value = 1; xioctl(fd, VIDIOC_S_CTRL, &c);

    struct v4l2_requestbuffers rb;
    memset(&rb, 0, sizeof rb);
    rb.count = 4; rb.type = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE; rb.memory = V4L2_MEMORY_DMABUF;
    if (xioctl(fd, VIDIOC_REQBUFS, &rb) < 0) DIE("REQBUFS entree");
    int nout = rb.count;
    if (nout > MAXB) DIE("trop de tampons d'entree");
    int infd[MAXB]; uint8_t *inmap[MAXB];
    for (int i = 0; i < nout; i++) {
        if ((infd[i] = dmabuf_alloc(heap, insize)) < 0) DIE("DMABUF entree");
        inmap[i] = mmap(NULL, insize, PROT_READ | PROT_WRITE, MAP_SHARED, infd[i], 0);
        if (inmap[i] == MAP_FAILED) DIE("mmap entree");
    }
    memset(&rb, 0, sizeof rb);
    rb.count = 4; rb.type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE; rb.memory = V4L2_MEMORY_DMABUF;
    if (xioctl(fd, VIDIOC_REQBUFS, &rb) < 0) DIE("REQBUFS sortie");
    int ncap = rb.count;
    if (ncap > MAXB) DIE("trop de tampons de sortie");
    int capfd[MAXB]; uint8_t *capmap[MAXB];
    for (int i = 0; i < ncap; i++) {
        if ((capfd[i] = dmabuf_alloc(heap, capsize)) < 0) DIE("DMABUF sortie");
        capmap[i] = mmap(NULL, capsize, PROT_READ, MAP_SHARED, capfd[i], 0);
        if (capmap[i] == MAP_FAILED) DIE("mmap sortie");
        struct v4l2_plane pl; struct v4l2_buffer b;
        memset(&pl, 0, sizeof pl); memset(&b, 0, sizeof b);
        b.type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE; b.memory = V4L2_MEMORY_DMABUF;
        b.index = i; b.length = 1; b.m.planes = &pl;
        pl.m.fd = capfd[i]; pl.length = capsize;
        if (xioctl(fd, VIDIOC_QBUF, &b) < 0) DIE("QBUF sortie");
    }
    int t = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE; if (xioctl(fd, VIDIOC_STREAMON, &t) < 0) DIE("STREAMON entree");
    t = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE; if (xioctl(fd, VIDIOC_STREAMON, &t) < 0) DIE("STREAMON sortie");

    /* --- boucle cadencee --- */
    int freeout[MAXB], nfree = nout;
    for (int i = 0; i < nout; i++) freeout[i] = i;
    int sent = 0, got = 0, late = 0;
    size_t total = 0;
    double tcap = 0, tconv = 0, tlat = 0, period = 1.0 / FPS;
    double sent_at[1 << 12];
    double c0 = cpu_s(), t0 = now(), next = t0;
    while (now() - t0 < DUR || got < sent) {
        if (now() - t0 < DUR && nfree > 0 && now() >= next) {
            int i = freeout[--nfree];
            double a = now();
            XShmGetImage(dpy, root, img, 0, 0, AllPlanes);
            double b = now();
            dmabuf_sync(infd[i], DMA_BUF_SYNC_START | DMA_BUF_SYNC_WRITE);
            ARGBToNV12((const uint8_t *)img->data, img->bytes_per_line,
                       inmap[i], stride, inmap[i] + (size_t)stride * scan, stride, W, H);
            dmabuf_sync(infd[i], DMA_BUF_SYNC_END | DMA_BUF_SYNC_WRITE);
            double cc = now();
            tcap += b - a; tconv += cc - b;
            struct v4l2_plane pl; struct v4l2_buffer bb;
            memset(&pl, 0, sizeof pl); memset(&bb, 0, sizeof bb);
            bb.type = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE; bb.memory = V4L2_MEMORY_DMABUF; bb.index = i;
            bb.length = 1; bb.m.planes = &pl;
            pl.m.fd = infd[i]; pl.length = insize; pl.bytesused = insize;
            bb.timestamp.tv_sec = sent / FPS; bb.timestamp.tv_usec = (sent % FPS) * (1000000 / FPS);
            if (xioctl(fd, VIDIOC_QBUF, &bb) < 0) DIE("QBUF entree");
            sent_at[sent & 4095] = a;
            sent++;
            next += period;
            if (now() > next + period) { late++; next = now(); }
        }
        struct pollfd p = { .fd = fd, .events = POLLIN | POLLOUT };
        double wait = next - now();
        poll(&p, 1, wait > 0 ? (int)(wait * 1000) + 1 : 1);
        for (;;) {
            struct v4l2_plane pl; struct v4l2_buffer b;
            memset(&pl, 0, sizeof pl); memset(&b, 0, sizeof b);
            b.type = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE; b.memory = V4L2_MEMORY_DMABUF; b.length = 1; b.m.planes = &pl;
            if (xioctl(fd, VIDIOC_DQBUF, &b) < 0) break;
            freeout[nfree++] = b.index;
        }
        for (;;) {
            struct v4l2_plane pl; struct v4l2_buffer b;
            memset(&pl, 0, sizeof pl); memset(&b, 0, sizeof b);
            b.type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE; b.memory = V4L2_MEMORY_DMABUF; b.length = 1; b.m.planes = &pl;
            if (xioctl(fd, VIDIOC_DQBUF, &b) < 0) break;
            size_t n = pl.bytesused > pl.data_offset ? pl.bytesused - pl.data_offset : 0;
            if (n) {
                fwrite(capmap[b.index] + pl.data_offset, 1, n, out);
                total += n;
                tlat += now() - sent_at[got & 4095];
                got++;
            }
            pl.m.fd = capfd[b.index]; pl.bytesused = 0; pl.length = capsize;
            if (xioctl(fd, VIDIOC_QBUF, &b) < 0) DIE("re-QBUF sortie");
        }
    }
    double el = now() - t0, cpu = cpu_s() - c0;
    printf("RESULTAT : %d images %dx%d en %.1f s = %.1f im/s (cible %d), %d retards\n", got, W, H, el, got / el, FPS, late);
    printf("  capture XShm  : %.2f ms/image\n", 1000 * tcap / sent);
    printf("  conversion    : %.2f ms/image (libyuv BGRX->NV12)\n", 1000 * tconv / sent);
    printf("  latence capture->image encodee : %.1f ms en moyenne\n", 1000 * tlat / got);
    printf("  debit produit : %.1f Mbit/s\n", total * 8.0 / 1e6 / el);
    printf("  CPU total du programme : %.0f %% d'un coeur\n", 100 * cpu / el);
    fclose(out);
    t = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE; xioctl(fd, VIDIOC_STREAMOFF, &t);
    t = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE; xioctl(fd, VIDIOC_STREAMOFF, &t);
    XShmDetach(dpy, &si); shmdt(si.shmaddr);
    return 0;
}
