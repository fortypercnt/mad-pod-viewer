"""A simple Mad Pod Racing bot (both pods race): steer at the next checkpoint, corrected for drift.

Run it in the viewer with the command:  python examples/simple_bot.py
"""
import math
import sys

laps = int(input())
checkpoints = [tuple(map(int, input().split())) for _ in range(int(input()))]
boost_left = True
first_turn = True

while True:
    pods = [list(map(int, input().split())) for _ in range(4)]   # x y vx vy angle nextCheckPointId; mine first
    for x, y, vx, vy, angle, cp in pods[:2]:
        tx, ty = checkpoints[cp]
        aim_x, aim_y = tx - 3 * vx, ty - 3 * vy          # aim off the target to cancel the drift
        dist = math.hypot(tx - x, ty - y)
        off = abs((math.degrees(math.atan2(aim_y - y, aim_x - x)) - angle + 180) % 360 - 180)
        if first_turn:   # the pods have no heading yet and may face anywhere
            off = 0.0
        thrust = "0" if off > 90 else str(int(100 * min(1.0, dist / 1800 + 0.3) * math.cos(math.radians(off / 2))))
        if boost_left and off < 5 and dist > 5000:
            thrust, boost_left = "BOOST", False
        print(f"{int(aim_x)} {int(aim_y)} {thrust}")
    sys.stdout.flush()
    first_turn = False
