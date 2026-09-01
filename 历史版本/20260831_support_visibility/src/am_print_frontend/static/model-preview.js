"use strict";

// Render the receipt-verified binary STL itself; no generated reference image.
function showModelPreview(canvas, buffer) {
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
  const centre = low.map((v, i) => (v + high[i]) / 2);
  const extent = low.map((v, i) => high[i] - v);
  const radius = Math.hypot(...extent) / 2;
  if (radius <= 0) throw new Error("Empty model");
  let yaw = -0.3, pitch = 0.18, scheduled = false, drag = null;
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
    const projected = triangles.map((t) => t.map(transform));
    projected.sort((a, b) => (b[0][1] + b[1][1] + b[2][1]) - (a[0][1] + a[1][1] + a[2][1]));
    for (const points of projected) {
      const u = points[1].map((v, i) => v - points[0][i]);
      const v = points[2].map((v, i) => v - points[0][i]);
      const n = [u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0]];
      const light = Math.abs((n[0] * -0.3 + n[1] * -0.7 + n[2] * 0.65) / (Math.hypot(...n) || 1));
      ctx.fillStyle = `hsl(160, 42%, ${29 + 28 * light}%)`;
      ctx.beginPath();
      points.forEach((p, i) => { const x = width / 2 + p[0] * scale, y = height / 2 - p[2] * scale;
        if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y); });
      ctx.closePath(); ctx.fill();
    }
    ctx.fillStyle = "#b1c5c1"; ctx.font = "12px sans-serif";
    ctx.fillText(`最终模型 · ${extent.map((v) => v.toFixed(1)).join(" × ")} mm`, 14, height - 14);
  }
  function schedule() { if (!scheduled) { scheduled = true; requestAnimationFrame(draw); } }
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
}
