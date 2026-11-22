/**
 * @file src/platform/linux/v4l2_encode.h
 * @brief Declarations for the V4L2 mem2mem hardware H.264 encoder (Qualcomm msm_vidc).
 *
 * Les encodeurs V4L2 Qualcomm n'acceptent pas les tampons MMAP qu'utilise FFmpeg : ils exigent
 * des tampons DMABUF alloues dans /dev/dma_heap. Cet encodeur fonctionne donc hors de FFmpeg,
 * comme le chemin natif NVENC : conversion libyuv BGRA -> NV12 directement dans le tampon
 * DMABUF d'entree, encodage materiel, recuperation du flux H.264 en clair.
 */
#pragma once

// standard includes
#include <cstdint>
#include <memory>
#include <vector>

// local includes
#include "src/platform/common.h"
#include "src/video.h"

namespace platf::v4l2 {

  /**
   * @brief One compressed frame returned by the hardware encoder.
   */
  struct encoded_frame_t {
    std::vector<std::uint8_t> data;  ///< H.264 Annex-B payload (SPS/PPS prepended before IDR frames).
    std::int64_t frame_index = 0;  ///< Frame index assigned by the video pipeline.
    bool idr = false;  ///< True when the frame is a keyframe.
  };

  /**
   * @brief V4L2 mem2mem H.264 encoder working on DMABUF buffers.
   */
  class encoder_t {
  public:
    encoder_t();
    ~encoder_t();
    encoder_t(const encoder_t &) = delete;
    encoder_t &operator=(const encoder_t &) = delete;

    /**
     * @brief Open and configure the hardware encoder.
     *
     * @param config Client video configuration (size, framerate, bitrate in kbps).
     * @param colorspace Requested colorspace and range.
     * @return true when the encoder is ready; false when this configuration is unsupported.
     */
    bool init(const video::config_t &config, const video::sunshine_colorspace_t &colorspace);

    /**
     * @brief Convert a captured BGRA image into the next free hardware input buffer.
     *
     * @param img Captured image (32 bits per pixel).
     * @return 0 on success, nonzero on failure.
     */
    int load(const img_t &img);

    /**
     * @brief Submit the loaded buffer to the encoder and wait for the compressed frame.
     *
     * @param frame_index Frame index assigned by the video pipeline.
     * @param force_idr Force a keyframe for this frame.
     * @param out Receives the compressed frame.
     * @return true when a frame was produced.
     */
    bool encode(std::int64_t frame_index, bool force_idr, encoded_frame_t &out);

  private:
    struct impl_t;
    std::unique_ptr<impl_t> impl;
  };

}  // namespace platf::v4l2

namespace platf {

  /**
   * @brief Encode device backed by the V4L2 hardware encoder.
   */
  struct v4l2_encode_device_t: encode_device_t {
    /**
     * @brief Prepare the hardware encoder for a client configuration.
     *
     * @param client_config Client video configuration.
     * @param cs Colorspace metadata.
     * @return true when the encoder is ready.
     */
    bool init_encoder(const video::config_t &client_config, const video::sunshine_colorspace_t &cs) {
      return enc.init(client_config, cs);
    }

    /**
     * @brief Convert a captured frame into the encoder input buffer.
     *
     * @param img Captured image.
     * @return 0 on success.
     */
    int convert(img_t &img) override {
      return enc.load(img);
    }

    v4l2::encoder_t enc;  ///< Hardware encoder instance owned by this device.
  };

}  // namespace platf
