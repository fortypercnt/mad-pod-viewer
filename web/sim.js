// Mad Pod Racing turn simulation for the replay viewer (a port of the referee physics).
// Used to animate a turn with its collisions and to show the final turn, whose result the referee never sends.
// Each turn is re-synced from the logged state, so a small mismatch can only affect that one turn's animation.
"use strict";
const SIM = (() => {
  const POD_RSQ = 800 * 800, CP_RSQ = 600 * 600, FRICTION = 0.85, MIN_IMPULSE = 120, EPS = 0.00001;
  const BOOST = 650, MAX_THRUST = 200, SHIELD_TIMER = 4, DEG = Math.PI / 180;

  // Parses "x y thrust|SHIELD|BOOST [message]".
  function parseOutput(line) {
    const m = /^\s*(-?\d+)\s+(-?\d+)\s+(\S+)?(?:\s+(.*))?$/.exec(line || "");
    if (!m) return { x: 0, y: 0, kind: "invalid", thrust: 0, message: "" };
    const p = m[3] === undefined ? String(MAX_THRUST) : m[3];
    let kind = "thrust", thrust = 0;
    if (p === "SHIELD") kind = "shield";
    else if (p === "BOOST") kind = "boost";
    else thrust = parseInt(p, 10);
    return { x: +m[1], y: +m[2], kind, thrust, message: m[4] || "" };
  }

  function collisionTime(s, i, j) {
    const px = s.x[j] - s.x[i], py = s.y[j] - s.y[i];
    const pl2 = px * px + py * py;
    if (pl2 <= POD_RSQ) return 0;
    const vx = s.vx[j] - s.vx[i], vy = s.vy[j] - s.vy[i];
    const dot = px * vx + py * vy;
    if (dot > 0) return 10;
    const vl2 = vx * vx + vy * vy;
    const disc = dot * dot - vl2 * (pl2 - POD_RSQ);
    if (disc < 0) return 10;
    return (-dot - Math.sqrt(disc)) / vl2;
  }

  function bounce(s, a, b, heavy) {
    let nx = s.x[b] - s.x[a], ny = s.y[b] - s.y[a];
    let dd = Math.sqrt(nx * nx + ny * ny);
    nx /= dd; ny /= dd;
    const rvx = s.vx[a] - s.vx[b], rvy = s.vy[a] - s.vy[b];
    const m1 = heavy[a] ? 0.1 : 1.0, m2 = heavy[b] ? 0.1 : 1.0;
    let force = (nx * rvx + ny * rvy) / (m1 + m2);
    if (force < MIN_IMPULSE) force += MIN_IMPULSE; else force += force;
    const ix = nx * -force, iy = ny * -force;
    s.vx[a] += ix * m1; s.vy[a] += iy * m1;
    s.vx[b] -= ix * m2; s.vy[b] -= iy * m2;
    if (dd <= 800) {
      dd -= 800;
      s.x[a] += nx * -(-dd / 2 + EPS); s.y[a] += ny * -(-dd / 2 + EPS);
      s.x[b] += nx * (-dd / 2 + EPS); s.y[b] += ny * (-dd / 2 + EPS);
    }
    return force;
  }

  function crosses(p1x, p1y, p2x, p2y, cx, cy) {
    const dx = p2x - p1x, dy = p2y - p1y;
    let ppx = p1x, ppy = p1y;
    const pd2 = dx * dx + dy * dy;
    if (pd2 !== 0) {
      const u = ((cx - p1x) * dx + (cy - p1y) * dy) / pd2;
      if (u > 1) { ppx = p2x; ppy = p2y; }
      else if (u > 0) { ppx = p1x + u * dx; ppy = p1y + u * dy; }
    }
    ppx -= cx; ppy -= cy;
    return ppx * ppx + ppy * ppy < CP_RSQ;
  }

  // state: {x,y,vx,vy,angle (deg), next} arrays of 4, plus first (true on the first turn, where angle is -1 and
  // any heading is allowed) and optionally exact (the unrounded angles from the previous simulated turn) (pods in referee order: p0 pod0, p0 pod1,
  // p1 pod0, p1 pod1). acts: 4 parsed outputs. pods: per-pod extras {shieldTimer, boostUsed} (updated in place).
  // Returns {segments, collisions, passed, end} where segments[k] = {t, x, y, vx, vy} (motion is linear from t to
  // the next segment's t), end = the state after the turn (rounded, friction applied, angle as integer degrees).
  function turn(state, acts, pods, cps) {
    const s = { x: state.x.slice(), y: state.y.slice(), vx: state.vx.slice(), vy: state.vy.slice() };
    const angle = [], heavy = [], thrusts = [];
    for (let i = 0; i < 4; i++) {
      const a = acts[i], pod = pods[i];
      // The referee keeps the angle unrounded and only reports it rounded: continue from our own unrounded angle
      // while it agrees with the reported one.
      const first = state.first && state.angle[i] === -1;
      let ang = ((state.angle[i] % 360) + 360) % 360;
      const ex = state.exact ? state.exact[i] : null;
      if (ex != null && (((Math.round(ex) % 360) + 360) % 360) === ang) ang = ex;
      if (a.kind !== "invalid" && (a.x !== s.x[i] || a.y !== s.y[i])) {
        const want = Math.atan2(a.y - s.y[i], a.x - s.x[i]) / DEG;
        if (first) ang = want;
        else {
          let d = ((want - ang) % 360 + 540) % 360 - 180;
          if (d > 18) d = 18; else if (d < -18) d = -18;
          ang += d;
        }
      }
      ang = ((ang % 360) + 360) % 360;
      angle.push(ang);
      let thrust = a.kind === "thrust" ? a.thrust : 0;
      if (a.kind === "boost") { thrust = pod.boostUsed ? MAX_THRUST : BOOST; pod.boostUsed = true; }
      if (a.kind === "shield") pod.shieldTimer = SHIELD_TIMER;
      heavy.push(a.kind === "shield");
      if (pod.shieldTimer > 0) thrust = 0;
      thrusts.push(thrust);
      s.vx[i] += Math.cos(ang * DEG) * thrust;
      s.vy[i] += Math.sin(ang * DEG) * thrust;
    }
    const segments = [], collisions = [], passed = [0, 0, 0, 0], passes = [];
    const next = state.next.slice();
    const snap = (t) => segments.push({ t, x: s.x.slice(), y: s.y.slice(), vx: s.vx.slice(), vy: s.vy.slice() });
    let t = 1.0, now = 0;
    let curx = s.x.slice(), cury = s.y.slice();
    snap(0);
    for (let guard = 0; t > 0 && guard < 64; guard++) {
      let first = t, ci = 0, cj = 0;
      for (let i = 3; i > 0; i--) for (let j = i - 1; j >= 0; j--) {
        const tx = collisionTime(s, i, j);
        if (tx <= first) { first = tx; ci = i; cj = j; }
      }
      for (let i = 0; i < 4; i++) { s.x[i] += s.vx[i] * first; s.y[i] += s.vy[i] * first; }
      t -= first; now += first;
      if (ci !== cj) {
        const cx = (s.x[ci] + s.x[cj]) / 2, cy = (s.y[ci] + s.y[cj]) / 2;
        const force = bounce(s, ci, cj, heavy);
        collisions.push({ t: now, a: ci, b: cj, x: cx, y: cy, force });
      }
      for (let i = 0; i < 4; i++) {
        if (crosses(curx[i], cury[i], s.x[i], s.y[i], cps[next[i]][0], cps[next[i]][1])) {
          passed[i]++;
          passes.push({ i, cp: next[i], t: now });
          next[i] = (next[i] + 1) % cps.length;
        }
        curx[i] = s.x[i]; cury[i] = s.y[i];
      }
      if (t > 0) snap(now);
    }
    segments.push({ t: 1, x: s.x.slice(), y: s.y.slice(), vx: s.vx.slice(), vy: s.vy.slice() });
    const end = { x: [], y: [], vx: [], vy: [], angle: angle.map((v) => ((Math.round(v) % 360) + 360) % 360), exact: angle, next };
    for (let i = 0; i < 4; i++) {
      end.x.push(Math.floor(s.x[i] + 0.5));
      end.y.push(Math.floor(s.y[i] + 0.5));
      end.vx.push(Math.trunc(s.vx[i] * FRICTION));
      end.vy.push(Math.trunc(s.vy[i] * FRICTION));
      if (pods[i].shieldTimer > 0) pods[i].shieldTimer--;
    }
    return { segments, collisions, passed, passes, end, thrusts, heavy };
  }

  // Position of pod i at fraction f (0..1) of a simulated turn.
  function posAt(sim, i, f) {
    const seg = sim.segments;
    let k = 0;
    while (k + 1 < seg.length - 1 && seg[k + 1].t <= f) k++;
    const g = seg[k], dt = Math.min(f, seg[k + 1].t) - g.t;
    return [g.x[i] + g.vx[i] * Math.max(0, dt), g.y[i] + g.vy[i] * Math.max(0, dt)];
  }

  return { parseOutput, turn, posAt };
})();
if (typeof module !== "undefined") module.exports = SIM;
