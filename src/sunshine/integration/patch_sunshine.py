#!/usr/bin/env python3
"""Branche l'encodeur V4L2 materiel dans Sunshine. Chaque ancre doit exister exactement une fois."""
import sys, shutil, os

ROOT = "/usr/local/src/sunshine-build/Sunshine"
orig = ROOT + "/.orig-v4l2"
os.makedirs(orig, exist_ok=True)

def patch(path, edits):
    full = os.path.join(ROOT, path)
    bak = os.path.join(orig, path.replace("/", "__"))
    if not os.path.exists(bak):
        shutil.copy2(full, bak)
    s = open(bak).read()  # on repart toujours de l'original : le script est rejouable
    for name, anchor, new, mode in edits:
        n = s.count(anchor)
        if n != 1:
            sys.exit(f"ECHEC {path}: ancre '{name}' trouvee {n} fois (attendu 1)")
        if mode == "after":
            s = s.replace(anchor, anchor + new, 1)
        elif mode == "before":
            s = s.replace(anchor, new + anchor, 1)
        else:
            s = s.replace(anchor, new, 1)
        print(f"  ok  {path}: {name}")
    open(full, "w").write(s)

# ---------------------------------------------------------------- video.h
patch("src/video.h", [
    ("formats v4l2",
     "      encoder_platform_formats_t::pix_fmt_yuv444_10bit = pix_fmt_yuv444_10bit;\n    }\n  };\n",
     """
  /**
   * @brief Platform formats for the V4L2 hardware encoder (system memory, NV12, H.264 only).
   */
  struct encoder_platform_formats_v4l2: encoder_platform_formats_t {
    encoder_platform_formats_v4l2() {
      encoder_platform_formats_t::dev_type = platf::mem_type_e::system;
      encoder_platform_formats_t::pix_fmt_8bit = platf::pix_fmt_e::nv12;
      encoder_platform_formats_t::pix_fmt_10bit = platf::pix_fmt_e::p010;
      encoder_platform_formats_t::pix_fmt_yuv444_8bit = platf::pix_fmt_e::ayuv;
      encoder_platform_formats_t::pix_fmt_yuv444_10bit = platf::pix_fmt_e::yuv444p16;
    }
  };
""", "after"),
])

# -------------------------------------------------------------- video.cpp
SESSION = """  /**
   * @brief V4L2 hardware encode session (Qualcomm msm_vidc).
   */
  class v4l2_encode_session_t: public encode_session_t {
  public:
    explicit v4l2_encode_session_t(std::unique_ptr<platf::v4l2_encode_device_t> encode_device):
        device(std::move(encode_device)) {
    }

    int convert(platf::img_t &img) override {
      return device ? device->convert(img) : -1;
    }

    void request_idr_frame() override {
      force_idr = true;
    }

    void request_normal_frame() override {
      force_idr = false;
    }

    void invalidate_ref_frames(int64_t, int64_t) override {
      // No reference invalidation on this encoder: ask for a keyframe instead.
      force_idr = true;
    }

    bool encode_frame(int64_t frame_index, platf::v4l2::encoded_frame_t &out) {
      if (!device) {
        return false;
      }
      const bool idr = force_idr;
      force_idr = false;
      return device->enc.encode(frame_index, idr, out);
    }

  private:
    std::unique_ptr<platf::v4l2_encode_device_t> device;
    bool force_idr = false;
  };

"""

ENCODE_V4L2 = """  /**
   * @brief Encode one frame through the V4L2 hardware encoder and queue the resulting packet.
   */
  int encode_v4l2(int64_t frame_nr, v4l2_encode_session_t &session, safe::mail_raw_t::queue_t<packet_t> &packets, void *channel_data, std::optional<std::chrono::steady_clock::time_point> frame_timestamp) {
    platf::v4l2::encoded_frame_t frame;
    if (!session.encode_frame(frame_nr, frame) || frame.data.empty()) {
      BOOST_LOG(error) << "V4L2 encoder returned no packet";
      return -1;
    }

    auto packet = std::make_unique<packet_raw_generic>(std::move(frame.data), frame_nr, frame.idr);
    packet->channel_data = channel_data;
    packet->frame_timestamp = frame_timestamp;
    packets->raise(std::move(packet));

    return 0;
  }

"""

V4L2_ENCODER = """
  #ifdef SUNSHINE_BUILD_V4L2
  /**
   * @brief V4L2 hardware encoder (Qualcomm msm_vidc), H.264 only.
   */
  encoder_t v4l2 {
    "v4l2"sv,
    std::make_unique<encoder_platform_formats_v4l2>(),
    {
      {},  // Common options
      {},  // SDR-specific options
      {},  // HDR-specific options
      {},  // YUV444 SDR-specific options
      {},  // YUV444 HDR-specific options
      {},  // Fallback options
      "av1_v4l2"s,
      {},  // capabilities
    },
    {
      {},  // Common options
      {},  // SDR-specific options
      {},  // HDR-specific options
      {},  // YUV444 SDR-specific options
      {},  // YUV444 HDR-specific options
      {},  // Fallback options
      "hevc_v4l2"s,
      {},  // capabilities
    },
    {
      {},  // Common options
      {},  // SDR-specific options
      {},  // HDR-specific options
      {},  // YUV444 SDR-specific options
      {},  // YUV444 HDR-specific options
      {},  // Fallback options
      "h264_v4l2"s,
      {},  // capabilities
    },
    H264_ONLY  // flags
  };
  #endif  // SUNSHINE_BUILD_V4L2
"""

patch("src/video.cpp", [
    ("include",
     '#include "nvenc/nvenc_encoder.h"\n',
     '#ifdef SUNSHINE_BUILD_V4L2\n  #include "platform/linux/v4l2_encode.h"\n#endif\n', "after"),
    ("classe de session",
     "  /**\n   * @brief Context object used while synchronizing encode sessions.",
     "#ifdef SUNSHINE_BUILD_V4L2\n" + SESSION + "#endif\n\n", "before"),
    ("encoder_t v4l2",
     "  #endif  // SUNSHINE_BUILD_VULKAN\n",
     V4L2_ENCODER, "after"),
    ("liste des encodeurs",
     "    &vaapi,\n#endif\n",
     "    &vaapi,\n  #ifdef SUNSHINE_BUILD_V4L2\n    &v4l2,\n  #endif\n#endif\n", "replace"),
    ("encode_v4l2",
     "  /**\n   * @brief Encode one captured frame and queue packets for transmission.",
     "#ifdef SUNSHINE_BUILD_V4L2\n" + ENCODE_V4L2 + "#endif\n\n", "before"),
    ("dispatch encode()",
     "      return encode_nvenc(frame_nr, *nvenc_session, packets, channel_data, frame_timestamp);\n    }\n",
     "      return encode_nvenc(frame_nr, *nvenc_session, packets, channel_data, frame_timestamp);\n"
     "#ifdef SUNSHINE_BUILD_V4L2\n"
     "    } else if (auto v4l2_session = dynamic_cast<v4l2_encode_session_t *>(&session)) {\n"
     "      return encode_v4l2(frame_nr, *v4l2_session, packets, channel_data, frame_timestamp);\n"
     "#endif\n"
     "    }\n", "replace"),
    ("make_v4l2_encode_session",
     "    return std::make_unique<nvenc_encode_session_t>(std::move(encode_device));\n  }\n",
     "\n#ifdef SUNSHINE_BUILD_V4L2\n"
     "  std::unique_ptr<v4l2_encode_session_t> make_v4l2_encode_session(const config_t &client_config, std::unique_ptr<platf::v4l2_encode_device_t> encode_device) {\n"
     "    if (!encode_device->init_encoder(client_config, encode_device->colorspace)) {\n"
     "      return nullptr;\n"
     "    }\n"
     "    return std::make_unique<v4l2_encode_session_t>(std::move(encode_device));\n"
     "  }\n"
     "#endif\n", "after"),
    ("dispatch make_encode_session",
     "      return make_nvenc_encode_session(config, std::move(nvenc_encode_device));\n    }\n",
     "      return make_nvenc_encode_session(config, std::move(nvenc_encode_device));\n"
     "#ifdef SUNSHINE_BUILD_V4L2\n"
     "    } else if (dynamic_cast<platf::v4l2_encode_device_t *>(encode_device.get())) {\n"
     "      auto v4l2_encode_device = boost::dynamic_pointer_cast<platf::v4l2_encode_device_t>(std::move(encode_device));\n"
     "      return make_v4l2_encode_session(config, std::move(v4l2_encode_device));\n"
     "#endif\n"
     "    }\n", "replace"),
    ("make_encode_device",
     "      result = disp.make_nvenc_encode_device(pix_fmt);\n    }\n",
     "      result = disp.make_nvenc_encode_device(pix_fmt);\n"
     "#ifdef SUNSHINE_BUILD_V4L2\n"
     "    } else if (dynamic_cast<const encoder_platform_formats_v4l2 *>(encoder.platform_formats.get())) {\n"
     "      result = std::make_unique<platf::v4l2_encode_device_t>();\n"
     "#endif\n"
     "    }\n", "replace"),
])

# ------------------------------------------------------------- linux.cmake
patch("cmake/compile_definitions/linux.cmake", [
    ("bloc V4L2",
     'list(APPEND PLATFORM_TARGET_FILES\n        "${CMAKE_SOURCE_DIR}/src/platform/linux/publish.cpp"',
     """# V4L2 hardware encoder (Qualcomm msm_vidc) with libyuv conversion
option(SUNSHINE_ENABLE_V4L2 "Enable the V4L2 hardware encoder" ON)
if(SUNSHINE_ENABLE_V4L2)
    find_library(YUV_LIBRARY NAMES yuv REQUIRED)
    find_path(YUV_INCLUDE_DIR libyuv.h REQUIRED)
    list(APPEND SUNSHINE_DEFINITIONS SUNSHINE_BUILD_V4L2=1)
    include_directories(SYSTEM ${YUV_INCLUDE_DIR})
    list(APPEND PLATFORM_LIBRARIES ${YUV_LIBRARY})
    list(APPEND PLATFORM_TARGET_FILES
            "${CMAKE_SOURCE_DIR}/src/platform/linux/v4l2_encode.h"
            "${CMAKE_SOURCE_DIR}/src/platform/linux/v4l2_encode.cpp")
endif()

""", "before"),
])
print("PATCH OK")
