#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <X11/extensions/XShm.h>
#include <sys/ipc.h>
#include <sys/shm.h>
#include <stdio.h>
#include <stdlib.h>
/* compte les pixels blancs / noirs / autres d'une capture de l'ecran racine */
static void stats(const char *nom, XImage *im) {
    long w = 0, b = 0, o = 0, n = (long)im->width * im->height;
    for (int y = 0; y < im->height; y += 4) for (int x = 0; x < im->width; x += 4) {
        unsigned long p = XGetPixel(im, x, y) & 0xffffff;
        if (p == 0xffffff) w++; else if (p == 0) b++; else o++;
    }
    long t = w + b + o;
    printf("%-22s %dx%d  blancs %3ld%%  noirs %3ld%%  autres %3ld%%  (pixel central #%06lx)\n", nom, im->width, im->height,
           100*w/t, 100*b/t, 100*o/t, XGetPixel(im, im->width/2, im->height/2) & 0xffffff);
    (void)n;
}
int main(void) {
    Display *d = XOpenDisplay(NULL); if (!d) { puts("pas d'affichage"); return 1; }
    Window r = DefaultRootWindow(d); XWindowAttributes a; XGetWindowAttributes(d, r, &a);
    XImage *im = XGetImage(d, r, 0, 0, a.width, a.height, AllPlanes, ZPixmap);
    if (im) stats("XGetImage (classique)", im);
    if (!XShmQueryExtension(d)) { puts("XShm indisponible"); return 0; }
    XShmSegmentInfo si;
    XImage *sm = XShmCreateImage(d, a.visual, a.depth, ZPixmap, NULL, &si, a.width, a.height);
    si.shmid = shmget(IPC_PRIVATE, sm->bytes_per_line * sm->height, IPC_CREAT | 0600);
    si.shmaddr = sm->data = shmat(si.shmid, 0, 0); si.readOnly = False;
    if (si.shmid < 0 || si.shmaddr == (char *)-1) { puts("shmget/shmat echoue"); return 0; }
    XShmAttach(d, &si); XSync(d, False); shmctl(si.shmid, IPC_RMID, 0);
    for (long i = 0; i < (long)sm->bytes_per_line * sm->height; i++) sm->data[i] = 0x55; /* motif temoin */
    Status ok = XShmGetImage(d, r, sm, 0, 0, AllPlanes); XSync(d, False);
    printf("XShmGetImage status=%d\n", ok);
    stats("XShmGetImage (XShm)", sm);
    XShmDetach(d, &si); shmdt(si.shmaddr); return 0;
}
