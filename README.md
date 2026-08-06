# Concierge Commissions — GitHub Pages

Migración completa del dashboard de Google Apps Script a GitHub Actions + GitHub Pages.
Apps Script deja de participar después de copiar las dos credenciales de Shopify a GitHub.

## Credenciales secretas

Solo existen dos secretos del repositorio:

- `SHOPIFY_STORE`
- `SHOPIFY_TOKEN`

No coloque el token en ningún archivo del repositorio.

## Reglas incorporadas

- Cliente nuevo: 12%.
- Cliente recurrente: 8%.
- JW / Swede Venture Cost: 1% cuando se habilite en `config/special_customers.json`.
- CJ Affiliate, devolución y suscripción real: 0%.
- El tag general del cliente `subscription` ya no excluye compras normales.
- Se considera suscripción real cuando la orden tiene un tag exacto de suscripción o el line item contiene selling plan / subscription ID.
- `commissioneligible` continúa sobreescribiendo únicamente exclusiones de producto Dropship, Collective y Autoship.

## Archivos que debe editar

### `config/rep_tags.json`

Coloque los tags activos de representantes Concierge:

```json
{
  "rep_tags": ["LH", "NP"]
}
```

### `config/special_customers.json`

Abra el cliente JW en Shopify. Copie el número final de la URL `/customers/NUMERO` y reemplace el texto de ejemplo. Después cambie `enabled` a `true`:

```json
{
  "special_customers": [
    {
      "label": "JW / Swede Venture Cost",
      "shopify_customer_id": "1234567890",
      "email": "",
      "commission_rate": 0.01,
      "enabled": true
    }
  ]
}
```

El ID del cliente no es una credencial secreta. No se guarda en Actions Secrets.

## Subir al repositorio

Suba a la raíz del repositorio todo el contenido de este proyecto, incluyendo las carpetas ocultas `.github` y `.gitignore`.

La estructura final debe ser:

```text
.github/workflows/update-dashboard.yml
config/rep_tags.json
config/special_customers.json
docs/data/dashboard.json
docs/index.html
scripts/build_dashboard.py
.gitignore
README.md
requirements.txt
```

## Configurar GitHub Actions Secrets

En el repositorio abra:

`Settings → Secrets and variables → Actions → New repository secret`

Cree exactamente:

1. `SHOPIFY_STORE` con el valor `equestrian-labs.myshopify.com`.
2. `SHOPIFY_TOKEN` con un token privado vigente de Shopify.

## Ejecutar

Abra:

`Actions → Update Concierge Dashboard → Run workflow`

La Action consulta Shopify, calcula los datos y actualiza `docs/data/dashboard.json`. También se ejecuta automáticamente cada seis horas.

## Publicar GitHub Pages

Abra:

`Settings → Pages`

Seleccione:

- Source: `Deploy from a branch`
- Branch: `main`
- Folder: `/docs`

La dirección esperada para este repositorio será:

`https://equestrian-labs-dashboard.github.io/Concierge-Commissions_Corro/`

GitHub puede tardar unos minutos en publicar la primera versión.

## Seguridad

El frontend nunca recibe `SHOPIFY_TOKEN`. GitHub Pages publica el dashboard y su JSON de resultados. Como el repositorio y Pages son públicos, los nombres, órdenes y montos incluidos en ese JSON también serán públicos. Para restringirlos se necesita un hosting con autenticación; GitHub Pages público no ofrece acceso privado al dashboard.
