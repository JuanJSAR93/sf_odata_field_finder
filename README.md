# SF OData Field Finder

Script para analizar el `$metadata` de SAP SuccessFactors OData V2 y localizar propiedades directas, navegables y globales sin ejecutar GET sobre todas las entidades.

Consulta la guía completa de componentes, modos y ejemplos en [GUIA_USO_SF_ODATA_FIELD_FINDER.md](GUIA_USO_SF_ODATA_FIELD_FINDER.md).

## Interfaz gráfica

Ejecutar:

```powershell
python sf_odata_field_finder_gui.py
```

En Windows también puedes ejecutar con doble clic [start.cmd](start.cmd). El iniciador comprueba `tkinter`, instala `requests` si no existe y después abre la interfaz. Para cargar un XML desde el inicio:

```powershell
.\start.cmd --metadata .\sf_metadata.xml
```

Desde **Gestionar metadata** se puede descargar y guardar un XML OData o seleccionar un XML previamente descargado. La ventana principal permite elegir la entidad raíz mediante un selector editable que filtra opciones mientras se escribe.

Las búsquedas disponibles son por nombre de propiedad, tipo OData, `sap:picklist`, `sap:label`, ruta/patrón OData, entidad relacionada o listado de navegaciones. Los resultados incluyen ruta, profundidad, tipo de búsqueda, estado de consulta y una propuesta de consulta OData.

Junto a la profundidad máxima, **Rutas alternativas máximas** controla cuántos caminos distintos se conservan por estado al buscar por ruta/patrón OData (valor inicial: `3`); el recorrido prioriza las rutas más cortas. **Coincidencias máximas** limita los resultados mostrados por cualquier modo de búsqueda (valor inicial: `10`). El campo **Filtro de metadata** reduce los resultados por tipo, entidad destino o propiedades del destino; no ejecuta un `$filter` contra SAP.

En **Nombre de propiedad** puedes usar `*` para cualquier cantidad de caracteres y `?` para uno. Por ejemplo, `custom_*` encuentra propiedades como `custom_compatibilidad` y `custom_videoentrevista`.

La columna **Ruta** muestra la ruta OData sin repetir los nombres internos de `EntityType`. Su ancho se ajusta a la ruta más extensa de cada búsqueda; usa la barra horizontal de la tabla para recorrerla completa. Selecciona una o varias filas para generar la consulta recomendada exclusivamente con esos resultados. Haz clic en una celda y usa `Ctrl+C`, o clic derecho, para copiar su valor, la ruta o la fila completa.

Al hacer doble clic en un resultado se abre una ventana con las propiedades internas de la entidad, sus atributos `sap:label` y `sap:picklist`, y todas sus `NavigationProperty` con entidad destino, `EntitySet` y asociación. Si el resultado es una `NavigationProperty` (por ejemplo, `degreeNav`), también se abre automáticamente la pestaña de propiedades de la entidad destino (por ejemplo, `PicklistOption`, que contiene `externalCode`). El campo **Buscar en detalles** filtra propiedades y navegaciones de todas las pestañas abiertas. En esas tablas, haz clic en una celda y usa `Ctrl+C`, o clic derecho, para copiar la celda específica o la fila completa.

## Configuración

Definir las credenciales fuera del código:

```powershell
$env:SF_BASE_URL = "https://api19preview.sapsf.com/odata/v2"
$env:SF_USERNAME = "SFAPI@serviandinT1"
$env:SF_PASSWORD = "<contraseña>"
```

Dependencia obligatoria:

```powershell
pip install requests
```

`rich` es opcional y solo mejora el formato:

```powershell
pip install rich
```

Para búsquedas repetidas, guardar y reutilizar el metadata localmente:

```powershell
python sf_odata_field_finder.py --entity JobRequisition `
  --fields age --direct-only --exact `
  --metadata-cache .\work\sf_metadata.xml
```

Usa `--refresh-metadata` para descargarlo nuevamente. Las búsquedas navegacionales usan BFS y detienen la expansión al visitar cada entidad por primera vez.

## Búsqueda de campos

```powershell
python sf_odata_field_finder.py `
  --entity JobRequisition `
  --fields restoresourcerTeamAdminDefaults custom_videoentrevista `
  --max-depth 6
```

Para comprobar un nombre exacto únicamente dentro de la entidad raíz:

```powershell
python sf_odata_field_finder.py `
  --entity JobRequisition `
  --fields age `
  --direct-only `
  --exact
```

Guardar los resultados:

```powershell
python sf_odata_field_finder.py --entity JobRequisition `
  --fields restoresourcerTeamAdminDefaults custom_videoentrevista `
  --output resultado.json
```

En consola se puede agregar un filtro OData manual a la URL recomendada, incluyendo propiedades de una entidad relacionada:

```powershell
python sf_odata_field_finder.py `
  --entity JobRequisition `
  --fields age `
  --filter "jobReqId eq '2381' and status/externalCode eq 'Open'"
```

El filtro se incluye en la URL recomendada y `status` se agrega automáticamente a `$expand` cuando se usa como navegación.

## Búsqueda por ruta o directorio

En la interfaz selecciona **Ruta / patrón OData**. Si pegas una ruta que contiene `/` mientras está seleccionado **Nombre de propiedad**, la aplicación cambia a ese modo automáticamente. Una ruta que no inicia con la entidad raíz se interpreta como parcial, de modo que con raíz `JobRequisitionPosting` puedes buscar:

```text
rmk_region/externalCode
```

para encontrar `JobRequisitionPosting/jobRequisition/rmk_region/externalCode`.

El último segmento puede ser una propiedad simple o una `NavigationProperty`, como `customTypeHire`. El comodín `*` como segmento completo abarca cero o más directorios de navegación. Dentro de un nombre de directorio o de propiedad, `*` busca cualquier texto y `?` un carácter: por ejemplo, `*Nav/externalCode` coincide con `degreeNav/externalCode`, y `custom_*` con propiedades que comienzan por `custom_`. El siguiente patrón encuentra la propiedad final aunque varíen los directorios intermedios:

```text
*/jobRequisition/*/degreeNav/externalCode
```

También está disponible en consola:

```powershell
python sf_odata_field_finder.py --entity JobRequisitionPosting `
  --path-pattern "*/jobRequisition/*/degreeNav/externalCode" `
  --max-depth 8
```

## Otros modos

```powershell
python sf_odata_field_finder.py --entity JobRequisition --list-nav --max-depth 3
python sf_odata_field_finder.py --search-entity JobReqTemplate
python sf_odata_field_finder.py --entity JobRequisition --search-related-entity JobRequisitionPosting --max-depth 6
python sf_odata_field_finder.py --dump-entity JobRequisition
```

Para pruebas con certificados no confiables puede usarse `--insecure`; SSL permanece habilitado por defecto. Las entidades `JobReqTemplate_*` se reportan como potencialmente no consultables: aparecer en `$metadata` no garantiza que soporten GET.
