# 简约模式 + F10 老板键设计

## 背景
用户希望增加一个「简约模式」，减少弹幕视觉干扰，并增加 F10 老板键快速隐藏弹幕。

## 目标
1. 简约模式开启后：不显示头像、不显示图片、昵称和消息内容使用统一颜色。
2. F10 作为全局老板键，一键隐藏/显示弹幕。

## 详细设计

### 1. 配置扩展

在 `config.py` 的 `Display` 中新增：

```python
simple_mode: bool = False
```

### 2. 设置面板

在 `settings_window.py` 的「显示」页增加复选框「简约模式」。

### 3. 渲染层

在 `danmu_engine.py` 的 `update_config` 中读取 `simpleMode` 并保存到 `self.simple_mode`。

在 `overlay.py` 渲染时，当 `engine.simple_mode` 为 True：
- 忽略 `show_avatar` 和 `show_image`，强制不绘制头像和图片；
- 昵称颜色使用与消息内容相同的颜色（`self.theme["text"]`），不再使用主题中的 `nickname` 色；
- 消息内容颜色保持 `self.theme["text"]`（或红包使用 `red_packet`）。

### 4. F10 老板键

在 `main.py` 中：
- 创建第二个 `HotkeyManager` 实例专门监听 F10；
- 或在现有 `HotkeyManager` 基础上扩展支持多热键。

为简单起见，创建第二个 `HotkeyManager`（`boss_key_mgr`），注册 `"f10"`，回调调用 `self.toggle_danmu()`。

在 `MainWindow.__init__` 中初始化并注册：

```python
self.boss_key_mgr = HotkeyManager()
self.boss_key_mgr.register("f10", self.toggle_danmu)
```

在 `quit()` 中注销：

```python
self.boss_key_mgr.unregister()
```

### 5. 数据流

用户勾选「简约模式」 → `_emit_config_save` → `main._on_config_saved` → `overlay.update_config` → 渲染时强制简化。
用户按 F10 → `boss_key_mgr.triggered` → `toggle_danmu()` → 弹幕显示/隐藏切换。

## 测试计划
1. 勾选简约模式，确认头像、图片不显示，昵称和消息颜色一致。
2. 取消简约模式，恢复原有设置。
3. 按 F10，弹幕隐藏；再按 F10，弹幕显示。
