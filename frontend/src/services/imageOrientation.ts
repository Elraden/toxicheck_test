export function rotatedDimensions(width: number, height: number, turns: number) {
  return Math.abs(turns % 2) === 1 ? { width: height, height: width } : { width, height };
}

export async function prepareLabelImage(source: string, turns = 0): Promise<string> {
  const image = new Image();
  image.src = source;
  await image.decode();
  const scale = Math.min(1, 3000 / Math.max(image.naturalWidth, image.naturalHeight));
  const width = Math.max(1, Math.round(image.naturalWidth * scale));
  const height = Math.max(1, Math.round(image.naturalHeight * scale));
  const canvas = document.createElement('canvas');
  const size = rotatedDimensions(width, height, turns);
  canvas.width = size.width;
  canvas.height = size.height;
  const context = canvas.getContext('2d');
  if (!context) throw new Error('Не удалось подготовить фото');
  context.fillStyle = '#ffffff';
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.translate(canvas.width / 2, canvas.height / 2);
  context.rotate((turns % 4) * Math.PI / 2);
  context.drawImage(image, -width / 2, -height / 2, width, height);
  return canvas.toDataURL('image/jpeg', 0.92);
}
