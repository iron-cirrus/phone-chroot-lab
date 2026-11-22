/**
 * @file src/platform/linux/v4l2_encode.cpp
 * @brief V4L2 mem2mem hardware H.264 encoder (Qualcomm msm_vidc) using DMABUF buffers.
 */
// standard includes
#include <algorithm>
#include <cerrno>
#include <cstring>

// lib includes
#include <fcntl.h>
#include <libyuv.h>
#include <linux/dma-buf.h>
#include <linux/dma-heap.h>
#include <linux/videodev2.h>
#include <poll.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>

// local includes
#include "src/logging.h"
#include "v4l2_encode.h"

using namespace std::literals;

namespace platf::v4l2 {

  namespace {
    constexpr int max_buffers = 32;  ///< Upper bound of buffers the driver may impose.
    constexpr const char *encoder_device = "/dev/video33";  ///< Qualcomm msm_vidc encoder node.
    constexpr const char *heap_device = "/dev/dma_heap/system";  ///< System DMA heap used for all buffers.

    int xioctl(int fd, unsigned long request, void *arg) {
      int r;
      do {
        r = ioctl(fd, request, arg);
      } while (r < 0 && errno == EINTR);
      return r;
    }

    int heap_alloc(int heap, std::size_t len) {
      struct dma_heap_allocation_data a;
      std::memset(&a, 0, sizeof a);
      a.len = len;
      a.fd_flags = O_RDWR | O_CLOEXEC;
      if (xioctl(heap, DMA_HEAP_IOCTL_ALLOC, &a) < 0) {
        return -1;
      }
      return static_cast<int>(a.fd);
    }

    void dmabuf_sync(int fd, std::uint64_t flags) {
      struct dma_buf_sync s;
      s.flags = flags;
      xioctl(fd, DMA_BUF_IOCTL_SYNC, &s);
    }

    void set_ctrl(int fd, std::uint32_t id, std::int32_t value, const char *name) {
      struct v4l2_control c;
      c.id = id;
      c.value = value;
      if (xioctl(fd, VIDIOC_S_CTRL, &c) < 0) {
        BOOST_LOG(debug) << "V4L2: control "sv << name << " refused: "sv << std::strerror(errno);
      }
    }
  }  // namespace

  struct encoder_t::impl_t {
    struct in_buf_t {
      int fd = -1;
      std::uint8_t *map = nullptr;
      bool in_flight = false;
    };
    struct cap_buf_t {
      int fd = -1;
      std::uint8_t *map = nullptr;
    };

    int dev = -1;
    int heap = -1;
    int width = 0;
    int height = 0;
    int stride = 0;  ///< Bytes per row of the NV12 planes.
    int scan = 0;  ///< Rows of the luma plane (aligned by the driver).
    std::size_t in_size = 0;
    std::size_t cap_size = 0;
    bool full_range = false;
    bool ready = false;
    int fps = 60;

    std::vector<in_buf_t> in;
    std::vector<cap_buf_t> cap;
    int current = -1;  ///< Input buffer loaded by load() and not yet submitted.

    std::vector<std::uint8_t> chroma_u;  ///< Scratch for the planar U plane (full range path).
    std::vector<std::uint8_t> chroma_v;  ///< Scratch for the planar V plane (full range path).
    std::vector<std::uint8_t> scaled;  ///< Scratch for a rescaled capture.

    ~impl_t() {
      shutdown();
    }

    void shutdown() {
      if (dev >= 0) {
        int t = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE;
        xioctl(dev, VIDIOC_STREAMOFF, &t);
        t = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE;
        xioctl(dev, VIDIOC_STREAMOFF, &t);
      }
      for (auto &b : in) {
        if (b.map) {
          munmap(b.map, in_size);
        }
        if (b.fd >= 0) {
          close(b.fd);
        }
      }
      for (auto &b : cap) {
        if (b.map) {
          munmap(b.map, cap_size);
        }
        if (b.fd >= 0) {
          close(b.fd);
        }
      }
      in.clear();
      cap.clear();
      if (dev >= 0) {
        close(dev);
        dev = -1;
      }
      if (heap >= 0) {
        close(heap);
        heap = -1;
      }
      ready = false;
    }

    /** Give back input buffers the encoder has finished with. */
    void reclaim() {
      for (;;) {
        struct v4l2_plane pl;
        struct v4l2_buffer b;
        std::memset(&pl, 0, sizeof pl);
        std::memset(&b, 0, sizeof b);
        b.type = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE;
        b.memory = V4L2_MEMORY_DMABUF;
        b.length = 1;
        b.m.planes = &pl;
        if (xioctl(dev, VIDIOC_DQBUF, &b) < 0) {
          break;
        }
        if (b.index < in.size()) {
          in[b.index].in_flight = false;
        }
      }
    }

    int acquire_input() {
      for (int attempt = 0; attempt < 200; ++attempt) {
        reclaim();
        for (std::size_t i = 0; i < in.size(); ++i) {
          if (!in[i].in_flight) {
            return static_cast<int>(i);
          }
        }
        struct pollfd p = {dev, POLLOUT | POLLIN, 0};
        poll(&p, 1, 10);
      }
      return -1;
    }

    bool open_and_configure(const video::config_t &config, const video::sunshine_colorspace_t &cs) {
      width = config.width;
      height = config.height;
      fps = std::max(1, config.framerate);
      full_range = cs.full_range;

      dev = open(encoder_device, O_RDWR | O_NONBLOCK);
      if (dev < 0) {
        BOOST_LOG(warning) << "V4L2: cannot open "sv << encoder_device << ": "sv << std::strerror(errno);
        return false;
      }
      heap = open(heap_device, O_RDWR | O_CLOEXEC);
      if (heap < 0) {
        BOOST_LOG(warning) << "V4L2: cannot open "sv << heap_device << ": "sv << std::strerror(errno);
        return false;
      }

      struct v4l2_capability cap_info;
      if (xioctl(dev, VIDIOC_QUERYCAP, &cap_info) < 0) {
        BOOST_LOG(warning) << "V4L2: QUERYCAP failed"sv;
        return false;
      }

      // 1) compressed output (CAPTURE queue)
      struct v4l2_format fc;
      std::memset(&fc, 0, sizeof fc);
      fc.type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE;
      fc.fmt.pix_mp.width = width;
      fc.fmt.pix_mp.height = height;
      fc.fmt.pix_mp.pixelformat = V4L2_PIX_FMT_H264;
      fc.fmt.pix_mp.num_planes = 1;
      fc.fmt.pix_mp.plane_fmt[0].sizeimage = static_cast<std::uint32_t>(width) * height;
      if (xioctl(dev, VIDIOC_S_FMT, &fc) < 0) {
        BOOST_LOG(warning) << "V4L2: H.264 output format refused: "sv << std::strerror(errno);
        return false;
      }

      // 2) raw NV12 input (OUTPUT queue), with colorimetry hints for the SPS/VUI
      struct v4l2_format fo;
      std::memset(&fo, 0, sizeof fo);
      fo.type = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE;
      fo.fmt.pix_mp.width = width;
      fo.fmt.pix_mp.height = height;
      fo.fmt.pix_mp.pixelformat = V4L2_PIX_FMT_NV12;
      fo.fmt.pix_mp.num_planes = 1;
      fo.fmt.pix_mp.colorspace = V4L2_COLORSPACE_SMPTE170M;
      fo.fmt.pix_mp.ycbcr_enc = V4L2_YCBCR_ENC_601;
      fo.fmt.pix_mp.quantization = full_range ? V4L2_QUANTIZATION_FULL_RANGE : V4L2_QUANTIZATION_LIM_RANGE;
      if (xioctl(dev, VIDIOC_S_FMT, &fo) < 0) {
        BOOST_LOG(warning) << "V4L2: NV12 input format refused: "sv << std::strerror(errno);
        return false;
      }
      if (fo.fmt.pix_mp.num_planes != 1) {
        BOOST_LOG(warning) << "V4L2: NV12 in "sv << static_cast<int>(fo.fmt.pix_mp.num_planes) << " planes is not supported"sv;
        return false;
      }
      stride = fo.fmt.pix_mp.plane_fmt[0].bytesperline;
      in_size = fo.fmt.pix_mp.plane_fmt[0].sizeimage;
      scan = height;
      const int aligned = (height + 31) & ~31;
      if (static_cast<std::size_t>(stride) * aligned * 3 / 2 <= in_size) {
        scan = aligned;
      }
      xioctl(dev, VIDIOC_G_FMT, &fc);
      cap_size = fc.fmt.pix_mp.plane_fmt[0].sizeimage;

      struct v4l2_streamparm sp;
      std::memset(&sp, 0, sizeof sp);
      sp.type = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE;
      sp.parm.output.timeperframe.numerator = 1;
      sp.parm.output.timeperframe.denominator = fps;
      xioctl(dev, VIDIOC_S_PARM, &sp);

      set_ctrl(dev, V4L2_CID_MPEG_VIDEO_BITRATE_MODE, V4L2_MPEG_VIDEO_BITRATE_MODE_CBR, "bitrate mode");
      set_ctrl(dev, V4L2_CID_MPEG_VIDEO_BITRATE, std::min(config.bitrate, 200000) * 1000, "bitrate");
      // Keyframes seulement a la demande (client Moonlight, perte de paquets) : un IDR periodique toutes les 5 s fait une rafale
      // de debit a 60 i/s. On demande une GOP "infinie" (comme les autres encodeurs de Sunshine) et on retombe sur des valeurs
      // plus petites si le pilote refuse (on relit la valeur pour le savoir).
      {
        const std::int32_t wanted[] = {1 << 30, 0x7fffff, 0x7fff, fps * 60};
        for (std::int32_t g : wanted) {
          set_ctrl(dev, V4L2_CID_MPEG_VIDEO_GOP_SIZE, g, "GOP size");
          struct v4l2_control rc;
          std::memset(&rc, 0, sizeof rc);
          rc.id = V4L2_CID_MPEG_VIDEO_GOP_SIZE;
          if (xioctl(dev, VIDIOC_G_CTRL, &rc) == 0 && rc.value == g) {
            BOOST_LOG(info) << "V4L2: GOP size "sv << g << " (keyframes on demand only)"sv;
            break;
          }
        }
      }
      set_ctrl(dev, V4L2_CID_MPEG_VIDEO_B_FRAMES, 0, "B frames");
      set_ctrl(dev, V4L2_CID_MPEG_VIDEO_H264_PROFILE, V4L2_MPEG_VIDEO_H264_PROFILE_HIGH, "profile");
      set_ctrl(dev, V4L2_CID_MPEG_VIDEO_PREPEND_SPSPPS_TO_IDR, 1, "SPS/PPS before IDR");

      // 3) buffers: everything is DMABUF
      struct v4l2_requestbuffers rb;
      std::memset(&rb, 0, sizeof rb);
      rb.count = 4;
      rb.type = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE;
      rb.memory = V4L2_MEMORY_DMABUF;
      if (xioctl(dev, VIDIOC_REQBUFS, &rb) < 0 || rb.count > max_buffers) {
        BOOST_LOG(warning) << "V4L2: input buffers refused"sv;
        return false;
      }
      in.resize(rb.count);
      for (auto &b : in) {
        b.fd = heap_alloc(heap, in_size);
        if (b.fd < 0) {
          BOOST_LOG(warning) << "V4L2: DMA heap allocation failed"sv;
          return false;
        }
        void *m = mmap(nullptr, in_size, PROT_READ | PROT_WRITE, MAP_SHARED, b.fd, 0);
        if (m == MAP_FAILED) {
          BOOST_LOG(warning) << "V4L2: cannot map an input buffer"sv;
          return false;
        }
        b.map = static_cast<std::uint8_t *>(m);
      }

      std::memset(&rb, 0, sizeof rb);
      rb.count = 4;
      rb.type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE;
      rb.memory = V4L2_MEMORY_DMABUF;
      if (xioctl(dev, VIDIOC_REQBUFS, &rb) < 0 || rb.count > max_buffers) {
        BOOST_LOG(warning) << "V4L2: output buffers refused"sv;
        return false;
      }
      cap.resize(rb.count);
      for (std::size_t i = 0; i < cap.size(); ++i) {
        auto &b = cap[i];
        b.fd = heap_alloc(heap, cap_size);
        if (b.fd < 0) {
          return false;
        }
        void *m = mmap(nullptr, cap_size, PROT_READ, MAP_SHARED, b.fd, 0);
        if (m == MAP_FAILED) {
          return false;
        }
        b.map = static_cast<std::uint8_t *>(m);
        struct v4l2_plane pl;
        struct v4l2_buffer qb;
        std::memset(&pl, 0, sizeof pl);
        std::memset(&qb, 0, sizeof qb);
        qb.type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE;
        qb.memory = V4L2_MEMORY_DMABUF;
        qb.index = static_cast<std::uint32_t>(i);
        qb.length = 1;
        qb.m.planes = &pl;
        pl.m.fd = b.fd;
        pl.length = static_cast<std::uint32_t>(cap_size);
        if (xioctl(dev, VIDIOC_QBUF, &qb) < 0) {
          BOOST_LOG(warning) << "V4L2: cannot queue an output buffer: "sv << std::strerror(errno);
          return false;
        }
      }

      int t = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE;
      if (xioctl(dev, VIDIOC_STREAMON, &t) < 0) {
        BOOST_LOG(warning) << "V4L2: STREAMON (input) failed: "sv << std::strerror(errno);
        return false;
      }
      t = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE;
      if (xioctl(dev, VIDIOC_STREAMON, &t) < 0) {
        BOOST_LOG(warning) << "V4L2: STREAMON (output) failed: "sv << std::strerror(errno);
        return false;
      }

      chroma_u.resize(static_cast<std::size_t>((width + 1) / 2) * ((height + 1) / 2));
      chroma_v.resize(chroma_u.size());
      ready = true;
      BOOST_LOG(info) << "V4L2 hardware encoder ready: "sv << width << 'x' << height << " @ "sv << fps << " fps, "sv
                      << config.bitrate << " kbps, "sv << (full_range ? "full"sv : "limited"sv) << " range, driver "sv
                      << reinterpret_cast<const char *>(cap_info.card);
      return true;
    }

    int load(const img_t &img) {
      if (!ready) {
        return -1;
      }
      const std::uint8_t *src = img.data;
      int src_pitch = img.row_pitch;
      if (img.width != width || img.height != height) {
        scaled.resize(static_cast<std::size_t>(width) * height * 4);
        if (libyuv::ARGBScale(img.data, img.row_pitch, img.width, img.height, scaled.data(), width * 4, width, height, libyuv::kFilterBox) != 0) {
          return -1;
        }
        src = scaled.data();
        src_pitch = width * 4;
      }

      int idx = acquire_input();
      if (idx < 0) {
        BOOST_LOG(error) << "V4L2: no free input buffer"sv;
        return -1;
      }
      auto &b = in[idx];
      std::uint8_t *y = b.map;
      std::uint8_t *uv = b.map + static_cast<std::size_t>(stride) * scan;

      dmabuf_sync(b.fd, DMA_BUF_SYNC_START | DMA_BUF_SYNC_WRITE);
      int r;
      if (full_range) {
        // BT.601 full range ("JPEG"): planar chroma, then interleave into the NV12 UV plane
        const int cw = (width + 1) / 2;
        const int ch = (height + 1) / 2;
        r = libyuv::ARGBToJ420(src, src_pitch, y, stride, chroma_u.data(), cw, chroma_v.data(), cw, width, height);
        if (r == 0) {
          libyuv::MergeUVPlane(chroma_u.data(), cw, chroma_v.data(), cw, uv, stride, cw, ch);
        }
      } else {
        r = libyuv::ARGBToNV12(src, src_pitch, y, stride, uv, stride, width, height);
      }
      dmabuf_sync(b.fd, DMA_BUF_SYNC_END | DMA_BUF_SYNC_WRITE);
      if (r != 0) {
        return -1;
      }
      current = idx;
      return 0;
    }

    bool encode(std::int64_t frame_index, bool force_idr, encoded_frame_t &out) {
      if (!ready || current < 0) {
        return false;
      }
      if (force_idr) {
        set_ctrl(dev, V4L2_CID_MPEG_VIDEO_FORCE_KEY_FRAME, 1, "force keyframe");
      }
      {
        struct v4l2_plane pl;
        struct v4l2_buffer b;
        std::memset(&pl, 0, sizeof pl);
        std::memset(&b, 0, sizeof b);
        b.type = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE;
        b.memory = V4L2_MEMORY_DMABUF;
        b.index = static_cast<std::uint32_t>(current);
        b.length = 1;
        b.m.planes = &pl;
        pl.m.fd = in[current].fd;
        pl.length = static_cast<std::uint32_t>(in_size);
        pl.bytesused = static_cast<std::uint32_t>(in_size);
        b.timestamp.tv_sec = frame_index / fps;
        b.timestamp.tv_usec = (frame_index % fps) * (1000000 / fps);
        if (xioctl(dev, VIDIOC_QBUF, &b) < 0) {
          BOOST_LOG(error) << "V4L2: cannot queue the frame: "sv << std::strerror(errno);
          return false;
        }
        in[current].in_flight = true;
        current = -1;
      }

      for (int waited = 0; waited < 3000;) {
        struct pollfd p = {dev, POLLIN, 0};
        poll(&p, 1, 20);
        reclaim();
        struct v4l2_plane pl;
        struct v4l2_buffer b;
        std::memset(&pl, 0, sizeof pl);
        std::memset(&b, 0, sizeof b);
        b.type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE;
        b.memory = V4L2_MEMORY_DMABUF;
        b.length = 1;
        b.m.planes = &pl;
        if (xioctl(dev, VIDIOC_DQBUF, &b) < 0) {
          waited += 20;
          continue;
        }
        const std::size_t n = pl.bytesused > pl.data_offset ? pl.bytesused - pl.data_offset : 0;
        if (n > 0 && b.index < cap.size()) {
          dmabuf_sync(cap[b.index].fd, DMA_BUF_SYNC_START | DMA_BUF_SYNC_READ);
          out.data.assign(cap[b.index].map + pl.data_offset, cap[b.index].map + pl.data_offset + n);
          dmabuf_sync(cap[b.index].fd, DMA_BUF_SYNC_END | DMA_BUF_SYNC_READ);
          out.idr = (b.flags & V4L2_BUF_FLAG_KEYFRAME) != 0;
          out.frame_index = frame_index;
        }
        // hand the buffer back to the encoder
        struct v4l2_plane rp;
        struct v4l2_buffer rq;
        std::memset(&rp, 0, sizeof rp);
        std::memset(&rq, 0, sizeof rq);
        rq.type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE;
        rq.memory = V4L2_MEMORY_DMABUF;
        rq.index = b.index;
        rq.length = 1;
        rq.m.planes = &rp;
        rp.m.fd = cap[b.index].fd;
        rp.length = static_cast<std::uint32_t>(cap_size);
        if (xioctl(dev, VIDIOC_QBUF, &rq) < 0) {
          BOOST_LOG(error) << "V4L2: cannot recycle an output buffer: "sv << std::strerror(errno);
          return false;
        }
        if (n > 0) {
          return true;
        }
      }
      BOOST_LOG(error) << "V4L2: timeout waiting for the encoder"sv;
      return false;
    }
  };

  encoder_t::encoder_t():
      impl {std::make_unique<impl_t>()} {
  }

  encoder_t::~encoder_t() = default;

  bool encoder_t::init(const video::config_t &config, const video::sunshine_colorspace_t &colorspace) {
    // The hardware path only does 8-bit 4:2:0 H.264.
    if (config.videoFormat != 0 || config.dynamicRange != 0 || config.chromaSamplingType != 0) {
      return false;
    }
    if (config.width < 64 || config.height < 64 || config.width % 2 != 0 || config.height % 2 != 0) {
      return false;
    }
    if (colorspace.colorspace != video::colorspace_e::rec601 && colorspace.colorspace != video::colorspace_e::rec709) {
      return false;
    }
    if (colorspace.colorspace == video::colorspace_e::rec709) {
      BOOST_LOG(warning) << "V4L2: Rec. 709 requested, colors are encoded as Rec. 601"sv;
    }
    if (!impl->open_and_configure(config, colorspace)) {
      impl->shutdown();
      return false;
    }
    return true;
  }

  int encoder_t::load(const img_t &img) {
    return impl->load(img);
  }

  bool encoder_t::encode(std::int64_t frame_index, bool force_idr, encoded_frame_t &out) {
    return impl->encode(frame_index, force_idr, out);
  }

}  // namespace platf::v4l2
