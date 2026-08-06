# Concierge Commissions — GitHub Pages

Migración completa de Google Apps Script a GitHub Actions + GitHub Pages. Apps Script ya no participa.

## Fuente de datos

- Shopify: órdenes, clientes, productos y comisiones.
- Google Sheet de tags: columna A, desde la fila 2.
- Google Sheet configurado: `1vj7bVmMg2irf_pyYGdUCcYApBsf9og2gUqQ06cn7mN4`, `gid=0`.

La hoja debe estar compartida como **Cualquier persona con el enlace → Lector**. No requiere API key ni Service Account.

## Únicos Secrets

En `Settings → Secrets and variables → Actions` crear:

- `SHOPIFY_STORE`
- `SHOPIFY_TOKEN`

No guardar el token en archivos del repositorio.

## Reglas

- New Customer: 12%.
- Recurring: 8%.
- Cliente especial JW / Swede Venture Cost: 1%, al habilitarlo en `config/special_customers.json`.
- Suscripción real, CJ Affiliate y devoluciones: 0%.
- El tag general del cliente `subscription` no excluye una compra normal.
- `commissioneligible` solo puede sobreescribir exclusiones de producto Dropship, Collective o Autoship.

## Configurar el cliente especial al 1%

Editar `config/special_customers.json`, colocar el Customer ID real de Shopify y cambiar `enabled` a `true`:

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

## Estructura

```text
.github/workflows/update-dashboard.yml
config/special_customers.json
docs/data/dashboard.json
docs/index.html
scripts/build_dashboard.py
.gitignore
README.md
requirements.txt
```

## Ejecución

1. Subir todos los archivos a la raíz del repositorio.
2. Crear los dos Secrets.
3. Abrir `Actions → Update and Deploy Concierge Dashboard → Run workflow`.
4. En `Settings → Pages`, elegir **Source: GitHub Actions**.

Enlace esperado:

`https://equestrian-labs-dashboard.github.io/Concierge-Commissions_Corro/`

## Seguridad

GitHub Pages es público. El token queda oculto en Actions, pero el JSON publicado puede mostrar nombres, órdenes y montos. Para restringir esos datos se necesita hosting con autenticación.
