#include "plugins/AnimationPlugin.h"
#include "websocket.h"
#include <algorithm>
#include <utility>

#ifdef ESP32
#define ANIMATION_GUARD std::lock_guard<std::mutex> guard(framesMutex)
#else
#define ANIMATION_GUARD
#endif

static void sendUploadResult(JsonDocument &request, bool success, const char *message)
{
  JsonDocument response;
  response["event"] = "animationUpload";
  response["requestId"] = request["requestId"] | "";
  response["success"] = success;
  response["message"] = message;
  String output;
  serializeJson(response, output);
  sendWSMessage(output);
}

void AnimationPlugin::setup()
{
  bool empty;
  {
    ANIMATION_GUARD;
    step = 0;
    empty = customAnimationFrames.empty();
  }
  if (empty)
  {
    Screen.setPixel(7, 4, 1);
    Screen.setPixel(8, 4, 1);
    Screen.setPixel(7, 5, 1);
    Screen.setPixel(8, 5, 1);
    Screen.setPixel(7, 6, 1);
    Screen.setPixel(8, 6, 1);
    Screen.setPixel(7, 7, 1);
    Screen.setPixel(8, 7, 1);
    Screen.setPixel(7, 8, 1);
    Screen.setPixel(8, 8, 1);

    Screen.setPixel(7, 10, 1);
    Screen.setPixel(8, 10, 1);
    Screen.setPixel(7, 11, 1);
    Screen.setPixel(8, 11, 1);
  }
}

void AnimationPlugin::loop()
{
  std::vector<int> bits;
  int delayMs;
  {
    ANIMATION_GUARD;
    if (customAnimationFrames.empty())
    {
      return;
    }
    if (step >= customAnimationFrames.size())
    {
      step = 0;
    }
    bits = Screen.readBytes(customAnimationFrames[step]);
    step = (step + 1) % customAnimationFrames.size();
    delayMs = frameDelay;
  }

  // Draw and wait without holding the mutex: uploads must remain responsive,
  // even when an animation uses the maximum ten-second frame delay.
  for (size_t i = 0; i < bits.size(); i++)
  {
    Screen.setPixelAtIndex(i, bits[i]);
  }

  Screen.presentAndWait(delayMs);
}

void AnimationPlugin::websocketHook(JsonDocument &request)
{
  const char *event = request["event"];
  if (event == nullptr)
  {
    return;
  }

  if (!strcmp(event, "resetFrames") || !strcmp(event, "configAnimation"))
  {
    ANIMATION_GUARD;
    if (request["frameDelay"].is<int>())
    {
      frameDelay = request["frameDelay"].as<int>();
      if (frameDelay < 10)
        frameDelay = 10;
      if (frameDelay > 10000)
        frameDelay = 10000;
    }
    if (!strcmp(event, "resetFrames"))
    {
      customAnimationFrames.clear();
      step = 0;
    }
    return;
  }

  if (!strcmp(event, "addFrame"))
  {
    ANIMATION_GUARD;
    if (customAnimationFrames.size() >= MAX_FRAMES)
    {
      Serial.println(F("Animation: frame limit reached, frame ignored"));
      return;
    }

    std::vector<int> frame(32);
    for (int k = 0; k < 32; k++)
    {
      frame[k] = request["data"][k].as<int>();
    }
    customAnimationFrames.push_back(std::move(frame));
  }
  else if (!strcmp(event, "upload"))
  {
    int size = request["screens"] | -1;
    if (size < 0 || size > (int)MAX_FRAMES)
    {
      sendUploadResult(request, false, "Animation must contain 0 to 128 frames");
      return;
    }

    if (!request["data"].is<JsonArray>() || request["data"].size() != (size_t)size)
    {
      sendUploadResult(request, false, "Animation frame count does not match data");
      return;
    }

    // Assemble the complete replacement before taking the lock, then publish
    // it in one swap so loop() never observes a partially initialized frame.
    std::vector<std::vector<int>> frames(size, std::vector<int>(32));
    for (int i = 0; i < size; i++)
    {
      if (!request["data"][i].is<JsonArray>() || request["data"][i].size() != 32)
      {
        sendUploadResult(request, false, "Each animation frame must contain 32 bytes");
        return;
      }
      for (int k = 0; k < 32; k++)
      {
        if (!request["data"][i][k].is<int>() || request["data"][i][k].as<int>() < 0 ||
            request["data"][i][k].as<int>() > 255)
        {
          sendUploadResult(request, false, "Animation frame bytes must be between 0 and 255");
          return;
        }
        frames[i][k] = request["data"][i][k].as<int>();
      }
    }

    {
      ANIMATION_GUARD;
      customAnimationFrames.swap(frames);
      step = 0;
      if (request["frameDelay"].is<int>())
      {
        frameDelay = std::max(10, std::min(10000, request["frameDelay"].as<int>()));
      }
    }
    sendUploadResult(request, true, "Animation uploaded successfully");
  }
  else if (!strcmp(event, "animationStatus"))
  {
    JsonDocument jsonDocument;
    jsonDocument["event"] = "animationStatus";
    {
      ANIMATION_GUARD;
      jsonDocument["screens"] = customAnimationFrames.size();
      jsonDocument["frameDelay"] = frameDelay;
    }
    String output;
    serializeJson(jsonDocument, output);
    sendWSMessage(output);
    jsonDocument.clear();
  }
}

const char *AnimationPlugin::getName() const
{
  return "Animation";
}
