#include "plugins/DDPPlugin.h"

#ifdef ESP32
#include <mutex>
static std::mutex ddpBufferLock;
#define DDP_BUFFER_GUARD std::lock_guard<std::mutex> guard(ddpBufferLock)
#else
#define DDP_BUFFER_GUARD
#endif

// Static pointer for callback access
static DDPPlugin *instance = nullptr;

// Frame buffer in DRAM for faster ISR access on ESP32
#ifdef ESP32
static DRAM_ATTR uint8_t ddpFrameBuffer[ROWS * COLS];
#else
static uint8_t ddpFrameBuffer[ROWS * COLS];
#endif

void DDPPlugin::applyBufferToScreen()
{
  DDP_BUFFER_GUARD;
  if (!bufferDirty)
  {
    return;
  }
  Screen.setRenderBuffer(ddpFrameBuffer, true);
  bufferDirty = false;
}

void DDPPlugin::setup()
{
  {
    DDP_BUFFER_GUARD;
    instance = this;
    memset(ddpFrameBuffer, 0, ROWS * COLS);
    bufferDirty = false;
  }

#ifdef ASYNC_UDP_ENABLED
  udp = new AsyncUDP();
  if (!udp->listen(4048))
  {
    Serial.println("DDP: Failed to listen on port 4048");
    delete udp;
    udp = nullptr;
    return;
  }

  Serial.println("DDP server listening at port: 4048");

  udp->onPacket([](AsyncUDPPacket packet) {
      DDP_BUFFER_GUARD;
      if (packet.length() < 10 || instance == nullptr)
        return;
      const uint8_t flags = packet.data()[0];
      const bool push = (flags & DDP_FLAG_PUSH) != 0;
      const uint8_t dataType = packet.data()[3];
      const uint8_t *data = packet.data() + 10; // Skip header
      const size_t dataLength = packet.length() - 10;

      // If packet has pixel data, write to buffer
      if (dataLength > 0)
      {
        if (dataType == DDP_TYPE_GRAYSCALE)
        {
          // 1 byte per pixel (0-255), direct copy
          int count = std::min((int)dataLength, ROWS * COLS);
          memcpy(ddpFrameBuffer, data, count);
        }
        else
        {
          // Legacy RGB mode: 3 bytes per pixel
          int count = std::min((int)(dataLength / 3), ROWS * COLS);
          if (count == 1)
          {
            // Single pixel mode - fill entire buffer
            uint16_t sum = data[0] + data[1] + data[2];
            uint8_t brightness = sum / 3;
            memset(ddpFrameBuffer, brightness, ROWS * COLS);
          }
          else
          {
            for (int i = 0; i < count; i++)
            {
              // Keep full white and low nonzero values when averaging RGB.
              uint16_t sum = data[i * 3] + data[i * 3 + 1] + data[i * 3 + 2];
              ddpFrameBuffer[i] = sum / 3;
            }
          }
        }
        instance->bufferDirty = true;
      }

      // Notify the plugin task; the network callback must not draw mid-present.
      if (push)
      {
        instance->bufferDirty = true;
      }
    });
#endif
}

void DDPPlugin::teardown()
{
  {
    DDP_BUFFER_GUARD;
    instance = nullptr;
  }
#ifdef ASYNC_UDP_ENABLED
  if (udp)
  {
    delete udp;
    udp = nullptr;
  }
#endif
}

void DDPPlugin::loop()
{
  // Apply buffer if dirty (for senders that don't use push flag)
  applyBufferToScreen();

#ifdef ESP32
  vTaskDelay(10);
#else
  delay(10);
#endif
}

const char *DDPPlugin::getName() const
{
  return "DDP";
}