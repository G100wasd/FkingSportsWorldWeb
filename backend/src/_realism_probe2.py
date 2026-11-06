import sys, math
sys.path.insert(0, '.')
from sportsworld.domain import geom, stadium, track
c_lat, c_lng = 22.90, 113.87; L, R = 84.0, 36.5
def outline(u):
    peri = 2 * L + 2 * math.pi * R; u %= peri
    if u < L: return (-L / 2 + u, R)
    u -= L
    if u < math.pi * R: a = u / R; return (L / 2 + R * math.sin(a), R * math.cos(a))
    u -= math.pi * R
    if u < L: return (L / 2 - u, -R)
    u -= L; a = u / R; return (-L / 2 - R * math.sin(a), -R * math.cos(a))
peri = 2 * L + 2 * math.pi * R
fence = [(c_lat + outline(k / 80 * peri)[1] / geom.MET_LAT,
          c_lng + outline(k / 80 * peri)[0] / geom.MET_LNG) for k in range(80)]
cps = [(c_lat + outline(s)[1] / geom.MET_LAT,
        c_lng + outline(s)[0] / geom.MET_LNG) for s in (60, 130, 200, 260, 330)]
ring = stadium.make_stadium_ring(cps, (cps[0][0], cps[0][1]), fence)
for seed in (1, 99, 777, 2024):
    trk = track.build_track(2200, 720, seed, cps, fence_ring=fence,
                            ring=(ring[0], ring[1], ring[2]),
                            start_ms=1760000000000)
    locs = trk['locations']
    stf = [w['value'] * 6.0 for w in trk['stepsPerTenSec'] if w['time'] > 0]
    alts = [p['bdA'] for p in locs]
    jumps = [math.hypot((b['gLng'] - a['gLng']) * geom.MET_LNG,
                        (b['gLat'] - a['gLat']) * geom.MET_LAT)
             for a, b in zip(locs, locs[1:])]
    print('seed=%d cad[%.0f~%.0f] alt=%.1fm maxJump=%.0fm steps=%d ascent=%d'
          % (seed, min(stf), max(stf), max(alts) - min(alts),
             max(jumps), trk['totalSteps'], trk['totalAscent']))
