export async function getProductByBarcode(
  barcode: string
): Promise<unknown> {
  const url = new URL(
    `https://world.openfoodfacts.org/api/v3.6/product/${encodeURIComponent(barcode)}.json`
  );

  url.searchParams.set(
    'fields',
    [
      'code',
      'product_name',
      'generic_name',
      'brands',
      'quantity',
      'categories',
      'categories_tags',
      'ingredients',
      'ingredients_text',
      'additives_tags',
      'additives_original_tags',
      'ingredients_analysis_tags',
      'allergens',
      'nutriments',
      'image_front_url'
    ].join(',')
  );

  const response = await fetch(url, {
    headers: {
      Accept: 'application/json'
    }
  });

  const data: unknown = await response.json();

  if (!response.ok && response.status !== 404) {
    throw new Error(`Open Food Facts вернул ошибку ${response.status}`);
  }

  return data;
}
