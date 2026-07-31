"""集中管理 danmuFishpi 的版本号。

所有需要展示版本号的地方都应从这里导入，避免散落、统一维护。
"""

APP_NAME = "弹幕鱼排"
APP_VERSION = "1.0.5"

# 发送给 FishPI 服务器的客户端标识（小尾巴包含版本号）
CLIENT_VERSION = f"Python/小梦的科技v{APP_VERSION}"
