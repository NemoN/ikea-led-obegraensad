#!/usr/bin/env python3
"""Run animation/scheduler firmware logic on the host with sanitizers.

Requires a C++ compiler and ArduinoJson downloaded by PlatformIO.
Run: python3 contrib/test_runtime_host.py
"""

from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]

COMMON = {
    "Arduino.h": r'''
        #pragma once
        #include <cstdint>
        #include <string>
        using String = std::string;
        #define F(value) value
        inline unsigned long clockMillis = 0;
        inline unsigned long millis() { return clockMillis; }
        struct SerialMock { void println(const char *) {} };
        inline SerialMock Serial;
    ''',
    "PluginManager.h": r'''
        #pragma once
        #include "Arduino.h"
        #include <ArduinoJson.h>
        #include <algorithm>
        #include <array>
        #include <cassert>
        #include <cstring>
        #include <functional>
        #include <map>
        #include <thread>
        #include <vector>
        class Plugin {
        public:
          virtual void setup() = 0;
          virtual void loop() {}
          virtual void websocketHook(JsonDocument &) {}
          virtual const char *getName() const = 0;
          virtual ~Plugin() = default;
        };
        struct ScreenMock {
          std::array<int, 256> pixels{};
          int lastDelay = 0;
          std::function<void()> onPresent;
          void setPixel(int, int, int) {}
          void setPixelAtIndex(int index, int value) { pixels.at(index) = value; }
          std::vector<int> readBytes(const std::vector<int> &bytes) {
            assert(bytes.size() == 32);
            assert(std::all_of(bytes.begin(), bytes.end(),
                   [&](int byte) { return byte == bytes[0]; }));
            std::vector<int> bits;
            for (int byte : bytes) {
              std::this_thread::yield();
              for (int bit = 7; bit >= 0; --bit) bits.push_back((byte >> bit) & 1);
            }
            return bits;
          }
          void presentAndWait(int delay) {
            lastDelay = delay;
            if (onPresent) onPresent();
          }
        };
        inline ScreenMock Screen;
        struct PluginManagerMock {
          std::vector<int> switches;
          void setActivePluginById(int id) { switches.push_back(id); }
        };
        inline PluginManagerMock pluginManager;
        struct StorageMock {
          bool opened = false, readOnly = false;
          int writes = 0;
          std::map<String, String> strings;
          std::map<String, int> integers;
          bool begin(const char *, bool ro = false) {
            assert(!opened); // Catch nested use of the global Preferences handle.
            opened = true;
            readOnly = ro;
            return true;
          }
          void end() { assert(opened); opened = false; }
          String getString(const char *key) {
            assert(opened);
            return strings[key];
          }
          void putString(const char *key, const String &value) {
            assert(opened && !readOnly);
            ++writes;
            strings[key] = value;
          }
          void putInt(const char *key, int value) {
            assert(opened && !readOnly);
            ++writes;
            integers[key] = value;
          }
          int getInt(const char *key, int fallback) {
            assert(opened);
            return integers.count(key) ? integers[key] : fallback;
          }
        };
        inline StorageMock storage;
    ''',
    "websocket.h": r'''
        #pragma once
        #include "Arduino.h"
        #include <vector>
        inline std::vector<String> responses;
        inline void sendWSMessage(String &message) { responses.push_back(message); }
    ''',
    "config.h": r'''
        #pragma once
        struct ConfigMock {
          bool autoStart = false;
          bool getAutoStartSchedule() const { return autoStart; }
        };
        inline ConfigMock config;
    ''',
}


class RuntimeHostTest(unittest.TestCase):
    def compile_and_run(self, source, firmware):
        compiler = shutil.which("c++")
        json_headers = next((ROOT / ".pio/libdeps").glob("*/ArduinoJson/src/ArduinoJson.h"), None)
        if compiler is None or json_headers is None:
            self.skipTest("Requires a C++ compiler and PlatformIO's ArduinoJson dependency")
        with tempfile.TemporaryDirectory(prefix="ikea-runtime-test-") as directory:
            temporary = Path(directory)
            for name, content in {**COMMON, "test.cpp": source}.items():
                (temporary / name).write_text(textwrap.dedent(content))
            # Keep the production declarations, replacing only their hardware includes.
            shutil.copyfile(ROOT / "include/scheduler.h", temporary / "scheduler.h")
            executable = temporary / "test-runtime"
            subprocess.run(
                [compiler, "-std=c++17", "-pthread", "-DESP32", "-DENABLE_STORAGE",
                 "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                 "-I", str(temporary), "-I", str(ROOT / "include"),
                 "-I", str(json_headers.parent), str(ROOT / firmware),
                 str(temporary / "test.cpp"), "-o", str(executable)],
                check=True, timeout=60,
            )
            subprocess.run([str(executable)], check=True, timeout=30)

    def test_scheduler_boot_uses_autostart_setting(self):
        self.compile_and_run(r'''
            #include "scheduler.h"
            #include "config.h"
            int main() {
              const String saved = R"([{"pluginId":2,"duration":3},{"pluginId":4,"duration":5}])";
              for (bool autoStart : {false, true}) {
                for (int previouslyActive : {0, 1}) {
                  Scheduler.clearSchedule();
                  Scheduler.schedule.clear();
                  storage.strings["schedule"] = saved;
                  storage.integers["scheduleactive"] = previouslyActive;
                  storage.writes = 0;
                  pluginManager.switches.clear();
                  config.autoStart = autoStart;
                  Scheduler.init();
                  assert(!storage.opened && storage.writes == 0);
                  assert(storage.strings["schedule"] == saved);
                  assert(Scheduler.schedule.size() == 2);
                  assert(Scheduler.schedule[0].duration == 3000);
                  assert(Scheduler.isActive == autoStart);
                  assert(pluginManager.switches.size() == (autoStart ? 1 : 0));
                  if (autoStart) {
                    clockMillis += 3000;
                    Scheduler.update();
                    assert(pluginManager.switches.back() == 4);
                  }
                }
              }
              for (const String invalid : {String(""), String("not json"), String("{}"), String("[]")}) {
                Scheduler.clearSchedule();
                Scheduler.schedule.clear();
                storage.strings["schedule"] = invalid;
                config.autoStart = true;
                Scheduler.init();
                assert(!Scheduler.isActive && Scheduler.schedule.empty());
              }
              assert(Scheduler.setScheduleByJSONString(saved));
              assert(storage.strings["schedule"] == saved);
              assert(!storage.opened && !Scheduler.isActive);
            }
        ''', "src/scheduler.cpp")

    def test_animation_upload_limits_acknowledgment_and_concurrency(self):
        self.compile_and_run(r'''
            #include "plugins/AnimationPlugin.h"
            #include "websocket.h"
            #include <atomic>
            JsonDocument upload(int count, int byte, int delay = 400) {
              JsonDocument request;
              request["event"] = "upload";
              request["requestId"] = "test-upload";
              request["screens"] = count;
              request["frameDelay"] = delay;
              JsonArray data = request["data"].to<JsonArray>();
              for (int frame = 0; frame < count; ++frame) {
                JsonArray bytes = data.add<JsonArray>();
                for (int i = 0; i < 32; ++i) bytes.add(byte);
              }
              return request;
            }
            bool accepted() {
              JsonDocument response;
              assert(!deserializeJson(response, responses.back()));
              assert(response["event"] == "animationUpload");
              assert(response["requestId"] == "test-upload");
              return response["success"].as<bool>();
            }
            int main() {
              AnimationPlugin plugin;
              plugin.setup();
              auto initial = upload(2, 255, 10000);
              plugin.websocketHook(initial);
              assert(accepted());
              plugin.loop();
              assert(Screen.lastDelay == 10000);
              for (int bit : Screen.pixels) assert(bit == 1);
              for (int count : {-1, 129}) {
                auto rejected = upload(count, 0, 10);
                plugin.websocketHook(rejected);
                assert(!accepted());
                plugin.loop();
                assert(Screen.lastDelay == 10000);
                for (int bit : Screen.pixels) assert(bit == 1);
              }
              auto invalid = upload(1, 256);
              plugin.websocketHook(invalid);
              assert(!accepted());
              auto mismatch = upload(1, 0);
              mismatch["screens"] = 2;
              plugin.websocketHook(mismatch);
              assert(!accepted());
              auto shortFrame = upload(1, 0);
              shortFrame["data"][0].as<JsonArray>().remove(0);
              plugin.websocketHook(shortFrame);
              assert(!accepted());
              auto maximum = upload(128, 0);
              plugin.websocketHook(maximum);
              assert(accepted());
              plugin.loop();
              for (int bit : Screen.pixels) assert(bit == 0);

              // Upload from another task while presentAndWait() is active.
              Screen.onPresent = [&] {
                std::thread writer([&] {
                  auto replacement = upload(1, 255);
                  plugin.websocketHook(replacement);
                });
                writer.join(); // Deadlocks if the frame mutex covers the delay.
              };
              plugin.loop();
              Screen.onPresent = {};

              std::atomic<bool> running{true};
              std::thread drawing([&] {
                while (running.load()) plugin.loop();
              });
              for (int n = 0; n < 500; ++n) {
                auto replacement = upload(n % 5 + 1, (n & 1) ? 255 : 0);
                plugin.websocketHook(replacement);
                JsonDocument reset;
                reset["event"] = "resetFrames";
                plugin.websocketHook(reset);
                JsonDocument append;
                append["event"] = "addFrame";
                JsonArray data = append["data"].to<JsonArray>();
                for (int i = 0; i < 32; ++i) data.add(255);
                plugin.websocketHook(append);
              }
              running.store(false);
              drawing.join();
            }
        ''', "src/plugins/AnimationPlugin.cpp")


if __name__ == "__main__":
    unittest.main()
