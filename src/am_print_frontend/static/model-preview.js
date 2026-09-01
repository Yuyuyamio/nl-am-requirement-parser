"use strict";

// Render the receipt-verified binary STL and actual G-code support paths.
function showModelPreview(canvas, buffer, supportPreview) {
  const view = new DataView(buffer);
  if (buffer.byteLength < 84) throw new Error("Invalid STL");
  const count = view.getUint32(80, true);
  if (!count || count > 250000 || 84 + count * 50 !== buffer.byteLength) throw new Error("Unsupported STL");
  const triangles = [];
  const low = [Infinity, Infinity, Infinity], high = [-Infinity, -Infinity, -Infinity];
  for (let i = 0; i < count; i++) {
    const points = [];
    for (let vertex = 0; vertex < 3; vertex++) {
      const point = [];
      for (let axis = 0; axis < 3; axis++) {
        const value = view.getFloat32(84 + i * 50 + 12 + vertex * 12 + axis * 4, true);
        if (!Number.isFinite(value)) throw new Error("Invalid vertex");
        point.push(value);
        low[axis] = Math.min(low[axis], value);
        high[axis] = Math.max(high[axis], value);
      }
      points.push(point);
    }
    triangles.push(points);
  }
  const supportPaths = supportPreview.paths.map((path) => {
    if (!Array.isArray(path) || path.length !== 8 || !path.slice(0, 7).every(Number.isFinite)) throw new Error("Invalid support path");
    const points = [[path[0], path[1], path[2]], [path[3], path[4], path[5]]];
    points.forEach((point) => point.forEach((value, axis) => {
      low[axis] = Math.min(low[axis], value); high[axis] = Math.max(high[axis], value);
    }));
    return { points, width: path[6], interface: Boolean(path[7]) };
  });
  const centre = low.map((v, i) => (v + high[i]) / 2);
  const extent = low.map((v, i) => high[i] - v);
  const radius = Math.hypot(...extent) / 2;
  if (radius <= 0) throw new Error("Empty model");
  let yaw = -0.3, pitch = 0.18, scheduled = false, drag = null, supportVisible = true, disposed = false;
  function draw() {
    scheduled = false;
    const width = Math.max(240, canvas.clientWidth);
    const height = 330;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = width * dpr; canvas.height = height * dpr;
    const ctx = canvas.getContext("2d");
    ctx.scale(dpr, dpr);
    ctx.fillStyle = "#10171c"; ctx.fillRect(0, 0, width, height);
    const scale = Math.min(width - 32, height - 52) / (radius * 2);
    const cy = Math.cos(yaw), sy = Math.sin(yaw), cp = Math.cos(pitch), sp = Math.sin(pitch);
    function transform(p) {
      const x = p[0] - centre[0], y = p[1] - centre[1], z = p[2] - centre[2];
      const a = cy * x - sy * y, b = sy * x + cy * y;
      return [a, cp * b + sp * z, cp * z - sp * b];
    }
    const projected = triangles.map((t) => ({ kind: "body", points: t.map(transform) }));
    if (supportVisible) supportPaths.forEach((path) => projected.push({
      kind: path.interface ? "interface" : "support",
      points: path.points.map(transform), width: path.width,
    }));
    projected.forEach((item) => { item.depth = item.points.reduce((sum, point) => sum + point[1], 0) / item.points.length; });
    projected.sort((a, b) => b.depth - a.depth);
    for (const item of projected) {
      const points = item.points;
      if (item.kind !== "body") {
        ctx.strokeStyle = item.kind === "interface" ? "rgba(255,173,69,.94)" : "rgba(65,214,138,.88)";
        ctx.lineWidth = Math.max(.65, Math.min(2.7, item.width * scale));
        ctx.beginPath(); ctx.moveTo(width / 2 + points[0][0] * scale, height / 2 - points[0][2] * scale);
        ctx.lineTo(width / 2 + points[1][0] * scale, height / 2 - points[1][2] * scale); ctx.stroke();
        continue;
      }
      const u = points[1].map((v, i) => v - points[0][i]);
      const v = points[2].map((v, i) => v - points[0][i]);
      const n = [u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0]];
      const light = Math.abs((n[0] * -0.3 + n[1] * -0.7 + n[2] * 0.65) / (Math.hypot(...n) || 1));
      ctx.fillStyle = supportVisible
        ? `hsla(197, 53%, ${34 + 25 * light}%, .62)`
        : `hsl(197, 53%, ${32 + 27 * light}%)`;
      ctx.beginPath();
      points.forEach((p, i) => { const x = width / 2 + p[0] * scale, y = height / 2 - p[2] * scale;
        if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y); });
      ctx.closePath(); ctx.fill();
    }
    ctx.fillStyle = "#b1c5c1"; ctx.font = "12px sans-serif";
    ctx.fillText(`最终打印预览 · ${supportPreview.segment_count.toLocaleString("zh-CN")} 条支撑刀路`, 14, height - 14);
  }
  function schedule() { if (!disposed && !scheduled) { scheduled = true; requestAnimationFrame(draw); } }
  canvas.onpointerdown = (event) => { drag = [event.clientX, event.clientY]; canvas.setPointerCapture(event.pointerId); };
  canvas.onpointermove = (event) => {
    if (!drag) return;
    yaw += (event.clientX - drag[0]) * 0.01;
    pitch = Math.max(-1.45, Math.min(1.45, pitch + (event.clientY - drag[1]) * 0.01));
    drag = [event.clientX, event.clientY]; schedule();
  };
  canvas.onpointerup = canvas.onpointercancel = () => { drag = null; };
  canvas._previewObserver?.disconnect();
  canvas._previewObserver = new ResizeObserver(schedule);
  canvas._previewObserver.observe(canvas);
  draw();
  return {
    setSupportVisible(value) { supportVisible = Boolean(value); schedule(); },
    setView(value) {
      if (value === "front") { yaw = 0; pitch = 0; }
      else if (value === "side") { yaw = -Math.PI / 2; pitch = 0; }
      else { yaw = -0.3; pitch = 0.18; }
      schedule();
    },
    dispose() { disposed = true; canvas._previewObserver?.disconnect(); },
  };
}
