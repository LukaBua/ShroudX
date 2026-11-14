/** Decode local PNG snapshots before playback, like the legacy HTML viewer. */
export async function preloadImages(paths: string[], signal: AbortSignal, progress: (count: number) => void): Promise<string[]> {
  const urls: string[] = [];
  try {
    for (const path of paths) {
      signal.throwIfAborted();
      const response = await fetch(path, { signal });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw Error(body?.error?.message || body?.detail || `Image request failed (${response.status}).`);
      }
      if (!response.headers.get('content-type')?.includes('image/png')) throw Error('The image endpoint did not return a PNG.');
      const url = URL.createObjectURL(await response.blob());
      urls.push(url);
      const image = new Image();
      image.src = url;
      await image.decode();
      signal.throwIfAborted();
      progress(urls.length);
    }
    return urls;
  } catch (error) {
    urls.forEach(URL.revokeObjectURL);
    throw error;
  }
}
