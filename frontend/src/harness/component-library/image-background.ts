/** Only exact opaque raster white is background; near-white and partial alpha survive. */
export function removeStrictWhiteBackground(pixels: Uint8ClampedArray): number {
  let removed = 0;
  for (let i = 0; i + 3 < pixels.length; i += 4) {
    if (pixels[i] === 255 && pixels[i + 1] === 255 && pixels[i + 2] === 255 && pixels[i + 3] === 255) {
      pixels[i + 3] = 0;
      removed++;
    }
  }
  return removed;
}
