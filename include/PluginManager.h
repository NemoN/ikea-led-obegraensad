#pragma once

#include <Arduino.h>
#include <ArduinoJson.h>
#ifdef ESP32
#include <atomic>
#endif
#include <string>
#include <vector>

#include "screen.h"
#include "signs.h"
#include "websocket.h"

class Plugin
{
private:
  int id;

public:
  Plugin();

  virtual ~Plugin()
  {
  }

  virtual void teardown();
  virtual void websocketHook(JsonDocument &request);
  virtual void setup() = 0;
  virtual void loop();
  virtual const char *getName() const = 0;

  void setId(int id);
  int getId() const;
};

class PluginManager
{
private:
  std::vector<Plugin *> plugins;
  Plugin *activePlugin = nullptr;
  int nextPluginId;
  int persistedPluginId = 1;
  // ESP32 receives requests from other tasks; ESP8266 callbacks run in loop().
#ifdef ESP32
  std::atomic<int> pendingPluginId{-1};
#else
  int pendingPluginId = -1;
#endif

  void renderPluginId(int pluginId);

public:
  PluginManager();

  int addPlugin(Plugin *plugin);
  void setActivePlugin(const char *pluginName);
  void setActivePluginById(int pluginId);
  void requestPluginById(int pluginId);  // Non-blocking request for async contexts
  void processPendingPluginChange();     // Called from the screen task to apply pending change
  void runActivePlugin();
  void setupActivePlugin();
  void activateNextPlugin();
  void persistActivePlugin();
  void init();
  void activatePersistedPlugin();
  int getPersistedPluginId();
  Plugin *getActivePlugin() const;
  std::vector<Plugin *> &getAllPlugins();
  size_t getNumPlugins();
};

extern PluginManager pluginManager;
