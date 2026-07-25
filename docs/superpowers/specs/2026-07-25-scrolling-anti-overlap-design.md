# 滚动模式防重叠 + 首行合并样式设计

## 背景

用户反馈弹幕「滚动模式」下短时间多条弹幕会重叠在一起,不便观看。代码审查定位到两个根因:

1. **速度 bug**:`danmu_engine.py` 的 `update_scrolling` 用固定 `duration` 配合 `total_distance = playfield_width + item.width` 计算位置,导致**越宽的弹幕像素速度越快**(约 400px 宽的比 100px 宽的快 15%),会从后面追上前面较窄的弹幕。而 `find_free_y` 的注释写着「所有弹幕同速、相对位置恒定」,这个前提是错的——入口时刻的无重叠检查根本保证不了全程无重叠。

2. **满屏兜底**:`find_free_y` 找不到空位时返回「最顶轨道 y」,新弹幕被强行塞到已有弹幕之上,直接叠在一起。

同时用户希望优化样式:让「头像 + 昵称 + 消息第一行文字」合并到同一行,压缩纵向空间;且图片位置与原始消息一致(可能上可能下),而非当前固定在文字上方。

## 目标

1. 滚动模式弹幕**全程不重叠**(数学上等价保证,而非靠运气)。
2. 满屏时新弹幕**排队等候、永不丢弃**。
3. 样式压缩:头像 + 昵称 + 第一行文字合并为首行,后续文字行/图片左对齐到「文字列」。
4. 图片位置**保留原始消息中的相对顺序**(可能在文字上方、下方或交错)。
5. 不影响浮动模式(`floating`)——其避让逻辑健全,不动。
6. 不新增配置项(`danmu_speed` 语义不变,队列行为固定)。

## 详细设计

### 1. 速度模型 — 统一像素速度(根治追尾)

**当前代码**(`danmu_engine.py`):
```python
item.duration = max(4.0, 25.0 - (self.danmu_speed / 100.0) * 21.0)
total_distance = self.playfield_width + item.width
item.x = playfield_right - progress * total_distance  # progress = elapsed / duration
```
宽度不同的弹幕 `duration` 相同但 `total_distance` 不同 → 像素速度 `v = total_distance / duration` 随宽度变化。

**改为**定义单一像素速度 `v`(px/秒),所有弹幕共用:
- `v = 80 + (danmu_speed / 100) * 140`,即线性映射到 `80~220 px/秒`;默认 `danmu_speed=50` → `v=150`(接近现状体感)
- 每条弹幕按自身宽度算时长:`duration = (playfield_width + item.width) / v`
- 位置:`x = playfield_right - v * elapsed`(等价于原公式,但 `v` 恒定)

**数学保证**:所有弹幕同速 → 相对水平位置恒定 → 入口时刻无重叠 = 全程无重叠,`find_free_y` 的入口检查重新变得可靠。

`danmu_speed` 设置项语义不变(0-100),用户无需重新调。

### 2. 入场队列 — 满屏时排队(永不丢)

**数据结构**:`DanmuEngine` 新增 `queued_items: list[DanmuItem]`(FIFO)。

**入场流程**(`_add_scrolling`):
```python
y = self.find_free_y(item)  # 返回 None 表示无空位(见第3节)
if y is None:
    self.queued_items.append(item)   # 入队,不进场
    return item                       # 仍返回 item,便于上游预取图片
item.y = y
item.start_time = time.time()
item.x = playfield_right
self.scroll_items.append(item)
return item
```

**补位**(`update_scrolling` 末尾新增 `_backfill_queue`):
```python
def _backfill_queue(self):
    now = time.time()
    kept = []
    for item in self.queued_items:           # FIFO 顺序
        item.start_time = now                # 进场时刻重置为补位时刻
        y = self.find_free_y(item)
        if y is None:
            kept.append(item)                # 仍满,留队
            break                            # 屏幕仍满,后续必然也无空位,停
        item.y = y
        item.x = playfield_right
        self.scroll_items.append(item)
    self.queued_items = kept
```
- FIFO 保证排队顺序 = 到达顺序,不插队
- 队列里的弹幕**图片已在 `overlay.add_message` 预取、高度已估算**(现有逻辑),补位时即可用
- **无上限、永不丢**:被持续刷屏时队列会增长、延迟变大,用户已接受;后续若想加告警/上限是独立小改动,本次不做

**注意**:`_backfill_queue` 在 `update_scrolling` 内调用,`update_scrolling` 每 tick 由 `overlay._tick` 触发,因此补位是 60fps 检查,轨道一空出来立刻有弹幕补上,延迟极小。

### 3. `find_free_y` 修正 — 返回 None 表示无空位

**当前代码**末尾:
```python
if self.tracks:
    return min(self.tracks, key=lambda t: t.y).y   # 强行塞顶 → 重叠根源
return float(self.playfield_top)
```

**改为**:
```python
return None   # 无空位,由调用方决定进场 or 入队
```

其余连续 y 扫描逻辑(`_overlaps` + 自顶向下 step 扫描)不变,仍支持多行/带图弹幕的变高放置。

调用方(`_add_scrolling`、`_backfill_queue`)据返回值决定进场 or 入队(见第2节)。

### 4. 样式压缩 — 首行合并 + 图片保留原始位置

这是本次改动量最大的一节。当前 `_parse_content` 把 `<img>` 从文本里剥离,返回 `(text_without_img_tags, img_urls)`,导致图片位置由代码硬编码决定(当前固定在文字上方)。要做到「和原始消息一致、可能上可能下」,必须保留图片在文本流中的位置。

#### 4.1 解析改造:`_parse_content` → `_parse_segments`

```python
def _parse_segments(self, content: str) -> list[Segment]:
    """返回有序块序列,保留 <img> 在文本流中的相对位置。

    连续的多个 <img> 合并为一个图片块(内部横向排列,沿用现有圆角样式);
    文本片段各自成块。块的顺序 = 原始消息中的出现顺序。
    """
```

`Segment` 是一个简单的联合类型:
- `TextSegment(text: str)`
- `ImageSegment(urls: list[str])`  # 连续多张图合并

例:
- `"文字<img>"` → `[Text("文字"), Image([url])]` → 图片在文字下方
- `"<img>文字"` → `[Image([url]), Text("文字")]` → 图片在文字上方
- `"<img>文字<img>"` → `[Image([url1]), Text("文字"), Image([url2])]` → 图上文下图

#### 4.2 布局:共用 `_layout_scrolling`

抽取一个共用布局函数,**测量和绘制都调它**,杜绝测量漂移导致的重叠:

```python
def _layout_scrolling(self, item: DanmuItem) -> ScrollLayout:
    """计算滚动弹幕的完整纵向布局。

    返回 ScrollLayout,含:
      - blocks: list[BlockLayout],每个块含 (type, y_offset, height, ...绘制参数)
      - total_h: 总高
      - content_w: 内容宽(用于 pixmap 尺寸)
      - prefix_w: 头像+昵称占用的首行前缀宽
      - text_column_x: 文字列左边界 = padding + prefix_w + gap
    """
```

**布局规则**(纵向从上到下,**按原始消息顺序**):
- `prefix_w` = 头像宽 + gap + 昵称(`昵称: `)宽 + gap
- `text_column_x` = `padding + prefix_w`(文字列左边界)
- 文字列可用宽 = `max_width - padding*2 - prefix_w`(右边界 `max_width - padding` 减左边界 `text_column_x`)
- **头像+昵称始终跟随第一个文本段**,合并到该文本段的第一行 `[头像][昵称: ][第一行文字]`;该段后续行左对齐到 `text_column_x`
- 图片块和其他文本段按原始顺序排列在第一个文本段前后;**图片可能在头像昵称行之上**(`<img>文字`)或之下(`文字<img>`),完全由原始消息决定
- 图片块左对齐到 `text_column_x`,内部横向排列(沿用 `_draw_inline_image` 圆角样式)
- `total_h` = 各块高度之和 + 块间距 + padding

示意图(`文字<img>` 情况):
```
[头像][昵称: ][第一行文字.........]   ← 首行(头像+昵称+第一文本段第一行)
            [第二行文字.........]   ← 后续行左对齐到文字列
            [第三行文字.........]
            [图片..............]   ← 图片块左对齐到文字列
```

`<img>文字` 情况:
```
            [图片..............]   ← 图片块在最上方(原始顺序)
[头像][昵称: ][第一行文字.........]   ← 头像+昵称跟随第一个文本段
            [第二行文字.........]
```

#### 4.3 测量与绘制统一

- `estimate_scrolling_size(item)` → 调 `_layout_scrolling`,返回 `(content_w, total_h)`
- `_get_item_pixmap(item)` → 调 `_layout_scrolling` 拿到 `blocks`,按 `blocks` 顺序绘制(头像/昵称/各文本行/各图片块)
- **两处不再各自重算**,从根上消除「估算能放下、实际画出来重叠」的漂移风险

首行合并省掉了原来独立的 prefix 行(`prefix_line_h`),纵向空间压缩约一行高度,顺带也降低了重叠压力。

### 5. 边界与配置

- **只改 `scrolling` 模式**;`floating` 模式完全不动(其 `_paint_floating` + `_find_free_y` 避让逻辑健全)
- **不新增配置项**:`danmu_speed` 语义不变(0-100),只是内部从「映射 duration」改成「映射 px/秒」;队列行为固定为「排队永不丢」
- **`max_float` 等浮动模式字段不动**
- `truncate_long_messages` / `max_message_lines` 截断逻辑保留,作用于文本段(首行合并后的总行数仍受 `max_message_lines` 限制)

### 6. 数据流

```
新消息到达
  → overlay.add_message(msg)
    → 预取图片(不变)
    → engine.add_message(msg)
      → _add_scrolling(item)
        → find_free_y(item)
          ├─ 有空位 → 进场(scroll_items),start_render_loop
          └─ 无空位 → 入队(queued_items)
  → 每 tick _tick()
    → engine.update_scrolling()
      → 按 v 更新 scroll_items 位置,移除出界
      → _backfill_queue():FIFO 尝试补位
    → repaint
```

## 涉及文件

| 文件 | 改动 |
|---|---|
| `danmu_engine.py` | 速度模型改 `v`;`find_free_y` 返回 `None`;新增 `queued_items` + `_backfill_queue`;`update_scrolling` 末尾调补位;`clear_all` 清队列 |
| `overlay.py` | `_parse_content` → `_parse_segments`;新增 `_layout_scrolling`;`estimate_scrolling_size` / `_get_item_pixmap` 改为调共用布局;`_get_item_pixmap` 按块顺序绘制(首行合并) |
| (无配置/设置/UI 改动) | — |

## 风险与缓解

1. **测量/绘制漂移**:首行合并 + 图片位置保留后,宽度/高度计算更复杂。**缓解**:第4.2 节强制 `estimate_scrolling_size` 和 `_get_item_pixmap` 共用 `_layout_scrolling`,从根上杜绝漂移。
2. **队列无限增长**:被持续刷屏时内存和延迟会涨。用户已接受「永不丢」;后续若想加告警/上限是独立小改动,本次不做。
3. **速度体感变化**:统一速度后,原来「宽弹幕更快」的体感会消失,个别弹幕看起来稍慢。中位速度校准到接近现状(~150 px/秒)。
4. **图片位置保留带来的布局复杂度**:图文交错情况(`图-文-图`)下 `total_h` 计算必须累加所有块。**缓解**:`_layout_scrolling` 一次算完所有块的 y 偏移和高度,绘制时按序消费。

## 测试计划

1. **速度一致性**:同屏放一条短弹幕和一条长弹幕(带图),观察两者是否保持恒定间距、全程不追尾。
2. **满屏排队**:连续发 30 条弹幕填满轨道,观察新弹幕是否排队等候、轨道空出后是否按 FIFO 补上、无重叠、无丢失。
3. **样式首行合并**:发一条普通文字弹幕,确认「头像+昵称+第一行文字」在同一行,后续行左对齐到文字列,纵向比改前少一行高度。
4. **图片位置保留**:
   - 发 `文字<img>` → 图片在文字下方
   - 发 `<img>文字` → 图片在文字上方
   - 发 `<img>文字<img>` → 图上文下图
5. **浮动模式回归**:切到 `floating` 模式,确认卡片避让/堆叠行为与改前一致。
6. **截断回归**:开启 `truncate_long_messages`,发超长代码块,确认首行合并后截断(含 `...`)仍正常、总行数受 `max_message_lines` 限制。
7. **测量=绘制**:同屏多条不同宽度的弹幕,确认无任何重叠(验证 `estimate_scrolling_size` 与 `_get_item_pixmap` 共用布局后无漂移)。
