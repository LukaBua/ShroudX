import { useEffect, useRef } from 'react';

/** Decorative illustration, not telescope imagery. Rendered once per resize. */
export default function Galaxy() {
  const canvas = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const el = canvas.current!, ctx = el.getContext('2d');
    if (!ctx) return;
    const draw = () => {
      let seed = 32;
      const random = () => { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 4294967296; };
      const w = el.clientWidth, h = el.clientHeight, dpr = Math.min(devicePixelRatio, 1.5);
      if (!w || !h) return;
      el.width = w * dpr; el.height = h * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, w, h);
      for (let i = 0; i < 700; i++) {
        ctx.fillStyle = `rgba(218,229,244,${.1 + random() * .55})`;
        ctx.beginPath(); ctx.arc(random() * w, random() * h, random() * .9, 0, Math.PI * 2); ctx.fill();
      }
      ctx.save(); ctx.translate(w * .61, h * .46); ctx.rotate(-.42); ctx.scale(1, .5);
      const size = Math.min(w * .53, h * .88);
      let glow = ctx.createRadialGradient(0, 0, 0, 0, 0, size);
      glow.addColorStop(0, 'rgba(246,206,166,.35)'); glow.addColorStop(.2, 'rgba(190,153,145,.2)');
      glow.addColorStop(.55, 'rgba(82,116,170,.15)'); glow.addColorStop(1, 'rgba(48,70,115,0)');
      ctx.fillStyle = glow; ctx.fillRect(-size, -size, size * 2, size * 2);
      ctx.globalCompositeOperation = 'screen';
      for (let i = 0; i < 21000; i++) {
        const r = Math.pow(random(), .7) * size;
        const angle = Math.floor(random() * 4) * Math.PI / 2 + r / size * 5.5 + (random() - .5) * (.25 + r / size * .65);
        const x = Math.cos(angle) * r + (random() - .5) * size * .04;
        const y = Math.sin(angle) * r + (random() - .5) * size * .04;
        const alpha = (.1 + random() * .45) * (1 - r / size * .7);
        ctx.fillStyle = r / size < .22 ? `rgba(255,220,181,${alpha})` : `rgba(${150 + random() * 75},${175 + random() * 60},255,${alpha})`;
        ctx.beginPath(); ctx.ellipse(x, y, random() * 1.25 + .2, random() * 1.8 + .25, 0, 0, Math.PI * 2); ctx.fill();
      }
      glow = ctx.createRadialGradient(0, 0, 0, 0, 0, size * .23);
      glow.addColorStop(0, 'rgba(255,248,223,.95)'); glow.addColorStop(.12, 'rgba(255,224,187,.8)');
      glow.addColorStop(.4, 'rgba(237,182,136,.3)'); glow.addColorStop(1, 'rgba(237,182,136,0)');
      ctx.fillStyle = glow; ctx.fillRect(-size, -size, size * 2, size * 2);
      ctx.restore(); ctx.globalCompositeOperation = 'source-over';
    };
    const observer = new ResizeObserver(draw); observer.observe(el); draw();
    return () => observer.disconnect();
  }, []);
  return <canvas ref={canvas} className="galaxy-canvas" aria-hidden="true" />;
}
