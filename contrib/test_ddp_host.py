#!/usr/bin/env python3
"""Exercise the real DDP plugin without a device: python3 contrib/test_ddp_host.py."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DDPHostTest(unittest.TestCase):
    def test_packet_handoff_and_brightness(self):
        compiler = shutil.which("c++")
        if compiler is None:
            self.skipTest("A C++ compiler is required")
        with tempfile.TemporaryDirectory(prefix="ikea-ddp-test-") as directory:
            temporary = Path(directory)
            files = {
                "PluginManager.h": r'''
                    #pragma once
                    #include <algorithm>
                    #include <cstdint>
                    #include <cstring>
                    constexpr int ROWS = 16, COLS = 16;
                    #define DRAM_ATTR
                    inline void vTaskDelay(int) {}
                    struct SerialMock { void println(const char *) {} };
                    inline SerialMock Serial;
                    class Plugin {
                    public:
                      virtual void setup() = 0;
                      virtual void teardown() {}
                      virtual void loop() {}
                      virtual const char *getName() const = 0;
                      virtual ~Plugin() = default;
                    };
                ''',
                "Screen.h": r'''
                    #pragma once
                    #include <array>
                    #include <cassert>
                    struct ScreenMock {
                      std::array<uint8_t, ROWS * COLS> pixels{};
                      int writes = 0;
                      void setRenderBuffer(const uint8_t *data, bool grays) {
                        assert(grays);
                        std::copy(data, data + pixels.size(), pixels.begin());
                        ++writes;
                      }
                    };
                    inline ScreenMock Screen;
                ''',
                "AsyncUDP.h": r'''
                    #pragma once
                    #include <functional>
                    #include <vector>
                    struct AsyncUDPPacket {
                      std::vector<uint8_t> bytes;
                      size_t length() const { return bytes.size(); }
                      const uint8_t *data() const { return bytes.data(); }
                    };
                    inline std::function<void(AsyncUDPPacket)> packetCallback;
                    inline bool listenSucceeds = true;
                    class AsyncUDP {
                    public:
                      bool listen(int) { return listenSucceeds; }
                      void onPacket(std::function<void(AsyncUDPPacket)> callback) {
                        packetCallback = callback;
                      }
                    };
                ''',
                "test.cpp": r'''
                    #include "plugins/DDPPlugin.h"
                    #include <cassert>
                    #include <iostream>
                    void deliver(uint8_t type, std::vector<uint8_t> data, bool push = true) {
                      std::vector<uint8_t> bytes(10);
                      bytes[0] = push ? DDP_FLAG_PUSH : 0;
                      bytes[3] = type;
                      bytes.insert(bytes.end(), data.begin(), data.end());
                      packetCallback({bytes});
                    }
                    int main() {
                      DDPPlugin plugin;
                      plugin.setup();
                      packetCallback({std::vector<uint8_t>(9, 255)});
                      plugin.loop();
                      assert(Screen.writes == 0);
                      deliver(DDP_TYPE_GRAYSCALE, {0, 1, 15, 128, 255});
                      assert(Screen.writes == 0); // No drawing from the network task.
                      plugin.loop();
                      assert(Screen.writes == 1);
                      assert(Screen.pixels[0] == 0 && Screen.pixels[1] == 1);
                      assert(Screen.pixels[2] == 15 && Screen.pixels[3] == 128);
                      assert(Screen.pixels[4] == 255);
                      plugin.loop();
                      assert(Screen.writes == 1); // An unchanged frame is not copied again.
                      deliver(DDP_TYPE_RGB, {255, 255, 255});
                      plugin.loop();
                      for (auto pixel : Screen.pixels) assert(pixel == 255);
                      deliver(DDP_TYPE_RGB, {0, 0, 0, 1, 1, 1}, false);
                      plugin.loop();
                      assert(Screen.pixels[0] == 0 && Screen.pixels[1] == 1);
                      deliver(DDP_TYPE_GRAYSCALE, std::vector<uint8_t>(300, 42), false);
                      plugin.loop();
                      for (auto pixel : Screen.pixels) assert(pixel == 42);
                      deliver(DDP_TYPE_GRAYSCALE, {}); // PUSH-only packet keeps the frame.
                      plugin.loop();
                      for (auto pixel : Screen.pixels) assert(pixel == 42);
                      plugin.teardown();
                      const int writes = Screen.writes;
                      deliver(DDP_TYPE_GRAYSCALE, {255}); // A late callback is ignored.
                      plugin.loop();
                      assert(Screen.writes == writes);
                      plugin.setup();
                      deliver(DDP_TYPE_GRAYSCALE, {7});
                      plugin.loop();
                      assert(Screen.pixels[0] == 7 && Screen.pixels[1] == 0);
                      plugin.teardown();
                      listenSucceeds = false;
                      plugin.setup();
                      plugin.teardown();
                      std::cout << "DDP packet handoff and brightness checks passed\n";
                    }
                ''',
            }
            for name, content in files.items():
                (temporary / name).write_text(textwrap.dedent(content))
            executable = temporary / "test-ddp"
            subprocess.run(
                [compiler, "-std=c++17", "-DESP32", "-fsanitize=address,undefined",
                 "-I", str(temporary), "-I", str(ROOT / "include"),
                 str(ROOT / "src/plugins/DDPPlugin.cpp"), str(temporary / "test.cpp"),
                 "-o", str(executable)],
                check=True,
            )
            subprocess.run([str(executable)], check=True)


if __name__ == "__main__":
    unittest.main()
