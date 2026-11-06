"""轨迹数据通道生成器包。

每个模块只负责一条提交数据通道的计算，由 domain/track.py（管理器）
统一编排汇总成 Track 字典送往前端/提交链路：

  util      公共小工具：加权抽样、时间轴采样、时间戳格式化
  altitude  海拔序列（分层模型）与总爬升
  speed     速度画像（起步/疲劳/波动/凹陷/停顿）、总距拟合、配速编码
  cadence   步频/步幅
  gps       GPS 点型序列、漂移、定位半径/状态、AR 抖动、瞬时速度毛刺
  motion    跑者走位：侧向摆动、向内收缩
  postfix   哨兵点与收尾修正（移植 postfix.rs）
  snap      打卡点吸附、平滑重拟合、弱吸附、尖角重拟合
  stats     窗口/分段重建与衍生指标（卡路里/功率/步频均值/配速）
"""

from . import (altitude, cadence, gps, motion, postfix,  # noqa: F401
               snap, speed, stats, util)
