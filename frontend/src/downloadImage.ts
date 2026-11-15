// AI-assisted: implemented with Codex and AI pair-programming assistant.

export type ExportMode = 'blink' | 'stack' | 'diff';

export interface ExportAnnotationOptions {
  mode: ExportMode;
  targetName?: string | null;
  ra?: number | null;
  dec?: number | null;
  detector?: number | string | null;
  fovArcsec?: number | null;
  wavelengthUm?: number | null;
  wavelengthRangeUm?: [number, number] | null;
  frameIndex?: number;
  frameCount?: number;
  frameDate?: string | null;
  epochALabel?: string | null;
  epochBLabel?: string | null;
  pmTotal?: number | null;
  pmTotalErr?: number | null;
  positionAngleDeg?: number | null;
  diffPalette?: 'color' | 'grayscale';
  synthetic?: boolean;
  smooth?: boolean;
  brightness?: number;
  contrast?: number;
  northUp?: boolean;
}

export interface ExportCaptionMeta {
  filename: string;
  modeBadge: string;
  targetTitle: string;
  coordsLine: string;
  detailsLine: string;
  legendText: string;
  motionLine: string | null;
  syntheticTag: string | null;
}

function slugPart(value: string | null | undefined, fallback: string): string {
  const cleaned = (value || '').trim().replace(/[^a-zA-Z0-9._-]+/g, '-').replace(/^-+|-+$/g, '');
  return cleaned.toLowerCase() || fallback;
}

export function buildExportCaption(opts: ExportAnnotationOptions): ExportCaptionMeta {
  const mode = opts.mode;
  const modeBadge =
    mode === 'diff'
      ? 'DIFFERENCE MODE · EPOCH B − EPOCH A'
      : mode === 'stack'
        ? 'STACK MODE · WEIGHTED CO-ADD'
        : 'BLINK MODE · TIME SEQUENCE';

  const rawName = (opts.targetName || '').trim();
  const hasCoords = typeof opts.ra === 'number' && Number.isFinite(opts.ra) && typeof opts.dec === 'number' && Number.isFinite(opts.dec);
  const coordsLine = hasCoords
    ? `ICRS RA ${(opts.ra as number).toFixed(4)}°  Dec ${(opts.dec as number) >= 0 ? '+' : ''}${(opts.dec as number).toFixed(4)}°`
    : 'Coordinates unavailable';
  const targetTitle = rawName
    ? rawName
    : hasCoords
      ? `Field ${(opts.ra as number).toFixed(3)}°, ${(opts.dec as number) >= 0 ? '+' : ''}${(opts.dec as number).toFixed(3)}°`
      : 'Selected SPHEREx Field';

  const parts: string[] = [];
  if (mode === 'blink') {
    const idx = (opts.frameIndex ?? 0) + 1;
    const total = opts.frameCount ?? 1;
    parts.push(`Frame ${idx}/${total}`);
    if (opts.frameDate) parts.push(opts.frameDate);
    if (typeof opts.wavelengthUm === 'number' && Number.isFinite(opts.wavelengthUm)) {
      parts.push(`${opts.wavelengthUm.toFixed(3)} µm`);
    }
  } else if (mode === 'stack') {
    if (opts.frameCount) parts.push(`${opts.frameCount} frames stacked`);
    if (opts.wavelengthRangeUm && opts.wavelengthRangeUm.length === 2) {
      parts.push(`${opts.wavelengthRangeUm[0].toFixed(2)}–${opts.wavelengthRangeUm[1].toFixed(2)} µm`);
    }
  } else {
    parts.push(`${opts.epochALabel || 'Earlier epoch'} → ${opts.epochBLabel || 'Later epoch'}`);
  }
  if (opts.detector !== null && opts.detector !== undefined && opts.detector !== '') {
    parts.push(`Detector ${opts.detector}`);
  }
  if (typeof opts.fovArcsec === 'number' && Number.isFinite(opts.fovArcsec)) {
    parts.push(`FOV ${Math.round(opts.fovArcsec)}″`);
  }

  const diffPalette = opts.diffPalette ?? 'color';
  const legendText =
    mode === 'diff'
      ? diffPalette === 'grayscale'
        ? 'Bright (+): brighter in later Epoch B (arrival) · Dark (−): brighter in earlier Epoch A (departure)'
        : 'Green (+): positive movement / brighter later · Red (−): negative movement / brighter earlier'
      : mode === 'stack'
        ? 'Orientation: North ↑, East ← · Weighted stack improves signal-to-noise ratio'
        : 'Orientation: North ↑, East ← · Shared stretch across frames highlights real motion';

  const motionLine =
    typeof opts.pmTotal === 'number' && Number.isFinite(opts.pmTotal)
      ? `Speed: ${opts.pmTotal.toFixed(2)} ± ${(opts.pmTotalErr ?? 0).toFixed(2)} ″/yr${
          typeof opts.positionAngleDeg === 'number' && Number.isFinite(opts.positionAngleDeg)
            ? ` · PA ${opts.positionAngleDeg.toFixed(1)}°`
            : ''
        }`
      : null;

  const targetSlug = slugPart(rawName, hasCoords ? `${(opts.ra as number).toFixed(2)}_${(opts.dec as number).toFixed(2)}` : 'field');
  const frameTag = mode === 'blink' ? `-f${(opts.frameIndex ?? 0) + 1}` : '';
  const filename = `shroudx-${targetSlug}-${mode}${frameTag}.png`;

  return {
    filename,
    modeBadge,
    targetTitle,
    coordsLine,
    detailsLine: parts.join(' · '),
    legendText,
    motionLine,
    syntheticTag: opts.synthetic ? 'SYNTHETIC / ILLUSTRATIVE' : null,
  };
}

function drawRoundedRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number,
): void {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + h - r);
  ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  ctx.lineTo(x + r, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}

export async function renderAnnotatedPngBlob(
  imageUrl: string,
  opts: ExportAnnotationOptions,
): Promise<{ blob: Blob; filename: string }> {
  const img = new Image();
  img.crossOrigin = 'anonymous';
  img.src = imageUrl;
  await img.decode();

  const caption = buildExportCaption(opts);
  const natW = Math.max(1, img.naturalWidth || img.width || 512);
  const natH = Math.max(1, img.naturalHeight || img.height || 512);
  const imgW = Math.max(600, natW);
  const imgH = Math.max(600, Math.round((imgW * natH) / natW));

  const headerH = 74;
  const footerH = caption.motionLine ? 88 : 68;
  const canvas = document.createElement('canvas');
  canvas.width = imgW;
  canvas.height = headerH + imgH + footerH;

  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('Canvas 2D context is unavailable for PNG export.');

  // Background
  ctx.fillStyle = '#080b10';
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  // Header strip
  ctx.fillStyle = '#0d131d';
  ctx.fillRect(0, 0, canvas.width, headerH);
  ctx.strokeStyle = 'rgba(214, 181, 141, 0.28)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(0, headerH - 0.5);
  ctx.lineTo(canvas.width, headerH - 0.5);
  ctx.stroke();

  // Mode badge pill
  ctx.font = '600 11px ui-monospace, SFMono-Regular, Menlo, monospace';
  const badgeW = Math.ceil(ctx.measureText(caption.modeBadge).width) + 20;
  drawRoundedRect(ctx, 16, 12, badgeW, 22, 4);
  ctx.fillStyle = opts.mode === 'diff' ? 'rgba(56, 239, 125, 0.14)' : 'rgba(214, 181, 141, 0.15)';
  ctx.fill();
  ctx.strokeStyle = opts.mode === 'diff' ? 'rgba(56, 239, 125, 0.5)' : 'rgba(214, 181, 141, 0.55)';
  ctx.stroke();
  ctx.fillStyle = opts.mode === 'diff' ? '#7df2ad' : '#f1d6b3';
  ctx.fillText(caption.modeBadge, 26, 27);

  if (caption.syntheticTag) {
    const synthW = Math.ceil(ctx.measureText(caption.syntheticTag).width) + 18;
    const sx = canvas.width - synthW - 16;
    drawRoundedRect(ctx, sx, 12, synthW, 22, 4);
    ctx.fillStyle = 'rgba(255, 160, 72, 0.16)';
    ctx.fill();
    ctx.strokeStyle = 'rgba(255, 160, 72, 0.5)';
    ctx.stroke();
    ctx.fillStyle = '#ffc38a';
    ctx.fillText(caption.syntheticTag, sx + 9, 27);
  }

  // Target / star name and coordinates
  ctx.font = '700 19px system-ui, -apple-system, Segoe UI, sans-serif';
  ctx.fillStyle = '#f5f1ea';
  ctx.fillText(caption.targetTitle, 16, 59);
  const nameW = ctx.measureText(caption.targetTitle).width;

  ctx.font = '500 12px ui-monospace, SFMono-Regular, Menlo, monospace';
  ctx.fillStyle = '#9aa8ba';
  ctx.fillText(caption.coordsLine, Math.min(canvas.width - 260, 28 + nameW), 58);

  // Main astronomical image
  ctx.save();
  const bright = opts.brightness ?? 100;
  const contrast = opts.contrast ?? 100;
  ctx.filter = `brightness(${bright}%) contrast(${contrast}%)`;
  ctx.imageSmoothingEnabled = Boolean(opts.smooth);
  ctx.drawImage(img, 0, headerH, imgW, imgH);
  ctx.restore();

  // Subtle orientation cue inside image corner
  if (opts.northUp !== false) {
    ctx.save();
    ctx.fillStyle = 'rgba(8, 11, 16, 0.72)';
    drawRoundedRect(ctx, 12, headerH + 12, 86, 22, 4);
    ctx.fill();
    ctx.font = '600 11px ui-monospace, SFMono-Regular, Menlo, monospace';
    ctx.fillStyle = '#d6b58d';
    ctx.fillText('N ↑  E ←', 22, headerH + 27);
    ctx.restore();
  }

  // Footer strip
  const fy = headerH + imgH;
  ctx.fillStyle = '#0d131d';
  ctx.fillRect(0, fy, canvas.width, footerH);
  ctx.strokeStyle = 'rgba(214, 181, 141, 0.25)';
  ctx.beginPath();
  ctx.moveTo(0, fy + 0.5);
  ctx.lineTo(canvas.width, fy + 0.5);
  ctx.stroke();

  ctx.font = '500 12px ui-monospace, SFMono-Regular, Menlo, monospace';
  ctx.fillStyle = '#dbe4ef';
  ctx.fillText(caption.detailsLine, 16, fy + 24);

  if (opts.mode === 'diff' && (opts.diffPalette ?? 'color') === 'color') {
    ctx.fillStyle = '#38ef7d';
    ctx.fillRect(16, fy + 38, 11, 11);
    ctx.fillStyle = '#c8f7dc';
    ctx.font = '500 11px system-ui, -apple-system, Segoe UI, sans-serif';
    ctx.fillText('+ Positive movement / brighter later (Epoch B)', 33, fy + 48);

    ctx.fillStyle = '#ff5252';
    ctx.fillRect(306, fy + 38, 11, 11);
    ctx.fillStyle = '#ffc7c7';
    ctx.fillText('− Negative movement / brighter earlier (Epoch A)', 323, fy + 48);
  } else {
    ctx.font = '500 11px system-ui, -apple-system, Segoe UI, sans-serif';
    ctx.fillStyle = '#a8b6c7';
    ctx.fillText(caption.legendText, 16, fy + 48);
  }

  if (caption.motionLine) {
    ctx.font = '500 11px ui-monospace, SFMono-Regular, Menlo, monospace';
    ctx.fillStyle = '#d6b58d';
    ctx.fillText(caption.motionLine, 16, fy + 71);
  }

  ctx.font = '600 10px ui-monospace, SFMono-Regular, Menlo, monospace';
  ctx.fillStyle = '#738296';
  const brand = 'ShroudX / SPHEREx';
  const brandW = ctx.measureText(brand).width;
  ctx.fillText(brand, canvas.width - brandW - 16, canvas.height - 14);

  const blob = await new Promise<Blob>((resolve, reject) => {
    canvas.toBlob((out) => {
      if (out) resolve(out);
      else reject(new Error('Failed to encode annotated PNG image.'));
    }, 'image/png');
  });
  return { blob, filename: caption.filename };
}

export async function triggerAnnotatedPngDownload(
  imageUrl: string,
  opts: ExportAnnotationOptions,
): Promise<string> {
  const { blob, filename } = await renderAnnotatedPngBlob(imageUrl, opts);
  const blobUrl = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = blobUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(blobUrl), 2000);
  return filename;
}
