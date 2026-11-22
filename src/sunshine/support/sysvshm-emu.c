/*
 * sysvshm-emu : emulation de la memoire partagee System V (shmget/shmat/shmdt/shmctl)
 * pour un noyau sans CONFIG_SYSVIPC (Android). A charger avec LD_PRELOAD.
 *
 * Un segment = un fichier dans /dev/shm/sysvshm-emu/<id>, projete en MAP_SHARED.
 * Tous les processus qui chargent la bibliotheque (serveur X + clients) voient
 * donc la meme memoire.
 *
 * IPC_RMID : le fichier est renomme en <id>.del (il reste attachable, comme sous
 * Linux ou un segment supprime reste accessible tant qu'il est attache) puis il
 * est efface quand le dernier processus attache s'en detache (ou meurt).
 * Le nombre d'attaches est suivi par des verrous OFD partages.
 */
#define _GNU_SOURCE
#include <sys/types.h>
#include <sys/ipc.h>
#include <sys/shm.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>
#include <time.h>

#define EMU_DIR "/dev/shm/sysvshm-emu"

/* Chaque attache garde un descripteur ouvert portant un verrou OFD partage :
 * tant qu'un processus est attache, un verrou exclusif est impossible.
 * Un processus qui meurt ferme ses descripteurs, donc relache ses verrous. */
struct att { void *addr; size_t size; int id; int fd; struct att *next; };
static struct att *atts;
static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
static unsigned counter;

static void ensure_dir(void)
{
    if (mkdir(EMU_DIR, 01777) == 0)
        chmod(EMU_DIR, 01777);
}

static void path_of(int id, int del, char *buf, size_t n)
{
    snprintf(buf, n, EMU_DIR "/%d%s", id, del ? ".del" : "");
}

static int open_id(int id, int flags)
{
    char p[128];
    path_of(id, 0, p, sizeof p);
    int fd = open(p, flags | O_CLOEXEC);
    if (fd < 0 && errno == ENOENT) {
        path_of(id, 1, p, sizeof p);
        fd = open(p, flags | O_CLOEXEC);
    }
    return fd;
}

static int lock_fd(int fd, short type)
{
    struct flock fl;
    memset(&fl, 0, sizeof fl);
    fl.l_type = type;
    fl.l_whence = SEEK_SET;
    return fcntl(fd, F_OFD_SETLK, &fl);
}

/* Efface un segment marque supprime si plus aucun processus n'y est attache. */
static void try_cleanup(int id)
{
    char pd[128];
    path_of(id, 1, pd, sizeof pd);
    int fd = open(pd, O_RDWR | O_CLOEXEC);
    if (fd < 0)
        return;
    if (lock_fd(fd, F_WRLCK) == 0)
        unlink(pd);
    close(fd);
}

static int new_segment(size_t size, int mode)
{
    ensure_dir();
    for (int tries = 0; tries < 1000; tries++) {
        unsigned r = (unsigned)getpid() * 2654435761u ^ (unsigned)time(NULL)
                   ^ (__atomic_add_fetch(&counter, 1, __ATOMIC_SEQ_CST) * 40503u);
        int id = (int)(r & 0x3fffffff) + 1;
        char p[128];
        path_of(id, 0, p, sizeof p);
        int fd = open(p, O_RDWR | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
        if (fd < 0) {
            if (errno == EEXIST)
                continue;
            return -1;
        }
        if (ftruncate(fd, (off_t)size) < 0) {
            int e = errno;
            close(fd);
            unlink(p);
            errno = e;
            return -1;
        }
        fchmod(fd, (mode & 0777) | 0600);
        close(fd);
        return id;
    }
    errno = ENOSPC;
    return -1;
}

int shmget(key_t key, size_t size, int shmflg)
{
    int mode = shmflg & 0777;
    if (key == IPC_PRIVATE) {
        if (size == 0) { errno = EINVAL; return -1; }
        return new_segment(size, mode);
    }
    ensure_dir();
    char kp[128], tgt[64];
    snprintf(kp, sizeof kp, EMU_DIR "/key-%d", (int)key);
    ssize_t n = readlink(kp, tgt, sizeof tgt - 1);
    if (n > 0) {
        tgt[n] = 0;
        if ((shmflg & IPC_CREAT) && (shmflg & IPC_EXCL)) { errno = EEXIST; return -1; }
        int id = atoi(tgt);
        int fd = open_id(id, O_RDONLY);
        if (fd >= 0) {
            struct stat st;
            fstat(fd, &st);
            close(fd);
            if (size > (size_t)st.st_size) { errno = EINVAL; return -1; }
            return id;
        }
        unlink(kp); /* lien perime */
    }
    if (!(shmflg & IPC_CREAT)) { errno = ENOENT; return -1; }
    if (size == 0) { errno = EINVAL; return -1; }
    int id = new_segment(size, mode);
    if (id < 0)
        return -1;
    snprintf(tgt, sizeof tgt, "%d", id);
    if (symlink(tgt, kp) < 0 && errno == EEXIST) {
        /* course avec un autre processus : on prend le segment existant */
        char p[128];
        path_of(id, 0, p, sizeof p);
        unlink(p);
        return shmget(key, size, shmflg & ~IPC_EXCL);
    }
    return id;
}

void *shmat(int shmid, const void *shmaddr, int shmflg)
{
    int ro = shmflg & SHM_RDONLY;
    int fd = open_id(shmid, ro ? O_RDONLY : O_RDWR);
    if (fd < 0) {
        if (errno == ENOENT) errno = EINVAL;
        return (void *)-1;
    }
    struct stat st;
    if (fstat(fd, &st) < 0) {
        int e = errno;
        close(fd);
        errno = e;
        return (void *)-1;
    }
    size_t sz = (size_t)st.st_size;
    int fl = MAP_SHARED;
    void *want = (void *)shmaddr;
    if (want && (shmflg & SHM_RND))
        want = (void *)((unsigned long)want & ~((unsigned long)SHMLBA - 1));
    if (want)
        fl |= MAP_FIXED;
    void *a = mmap(want, sz, PROT_READ | (ro ? 0 : PROT_WRITE), fl, fd, 0);
    if (a == MAP_FAILED) {
        int e = errno;
        close(fd);
        errno = e;
        return (void *)-1;
    }
    lock_fd(fd, F_RDLCK); /* marque "attache" ; fd garde ouvert jusqu'au shmdt */
    struct att *t = malloc(sizeof *t);
    if (!t) {
        close(fd);
    } else {
        t->addr = a;
        t->size = sz;
        t->id = shmid;
        t->fd = fd;
        pthread_mutex_lock(&mu);
        t->next = atts;
        atts = t;
        pthread_mutex_unlock(&mu);
    }
    return a;
}

int shmdt(const void *shmaddr)
{
    struct att **pp, *t = NULL;
    pthread_mutex_lock(&mu);
    for (pp = &atts; *pp; pp = &(*pp)->next) {
        if ((*pp)->addr == shmaddr) {
            t = *pp;
            *pp = t->next;
            break;
        }
    }
    pthread_mutex_unlock(&mu);
    if (!t) { errno = EINVAL; return -1; }
    munmap(t->addr, t->size);
    close(t->fd);      /* relache notre verrou partage */
    try_cleanup(t->id); /* efface si supprime et plus personne d'attache */
    free(t);
    return 0;
}

int shmctl(int shmid, int cmd, struct shmid_ds *buf)
{
    char p[128], pd[128];
    path_of(shmid, 0, p, sizeof p);
    path_of(shmid, 1, pd, sizeof pd);
    switch (cmd) {
    case IPC_STAT:
    case SHM_STAT: {
        struct stat st;
        int del = 0;
        if (stat(p, &st) < 0) {
            if (stat(pd, &st) < 0) { errno = EINVAL; return -1; }
            del = 1;
        }
        if (!buf) { errno = EFAULT; return -1; }
        memset(buf, 0, sizeof *buf);
        buf->shm_perm.__key = IPC_PRIVATE;
        buf->shm_perm.uid = st.st_uid;
        buf->shm_perm.gid = st.st_gid;
        buf->shm_perm.cuid = st.st_uid;
        buf->shm_perm.cgid = st.st_gid;
        buf->shm_perm.mode = (st.st_mode & 0777) | (del ? SHM_DEST : 0);
        buf->shm_segsz = (size_t)st.st_size;
        buf->shm_nattch = 1;
        buf->shm_atime = buf->shm_dtime = buf->shm_ctime = st.st_mtime;
        buf->shm_cpid = buf->shm_lpid = getpid();
        return 0;
    }
    case IPC_RMID:
        if (rename(p, pd) < 0 && access(pd, F_OK) < 0) { errno = EINVAL; return -1; }
        try_cleanup(shmid); /* comme Linux : libere tout de suite si personne n'est attache */
        return 0;
    case IPC_SET:
        return 0;
    default:
        errno = EINVAL;
        return -1;
    }
}
