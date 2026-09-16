# Guía de uso — SF OData Field Finder

## Propósito y alcance

La herramienta analiza el archivo `$metadata` de SAP SuccessFactors OData V2. Sirve para descubrir entidades, propiedades, `NavigationProperty`, tipos y rutas OData sin leer registros de SAP durante la búsqueda.

Puede descargar el metadata una vez o abrir un XML ya guardado. La consulta OData recomendada se construye localmente; copiarla o verla no ejecuta una petición GET.

## Inicio

En Windows abre [start.cmd](start.cmd). El iniciador detecta Python, comprueba `tkinter` e instala `requests` si falta.

También puede iniciarse con un metadata concreto:

```powershell
.\start.cmd --metadata .\sf_metadata.xml
```

## Componentes de la interfaz

| Componente | Para qué sirve |
|---|---|
| Gestionar metadata | Abre la ventana para seleccionar un XML local o descargar `$metadata`. |
| URL base | Dirección del tenant, por ejemplo `https://mi-tenant.sapsf.com/odata/v2`. |
| Usuario y contraseña | Se usan solo para descargar metadata. La contraseña no se guarda en el programa. |
| Entidad raíz | Punto desde el cual se calculan las rutas. Al cargar el XML se toma la primera entidad disponible. |
| Modo | Define qué atributo se busca: nombre, tipo, picklist, etiqueta, ruta, entidad relacionada o navegaciones. |
| Texto, patrón o ruta | Término principal de búsqueda. Admite comodines y rutas según el modo. |
| Profundidad máxima | Número máximo de `NavigationProperty` desde la entidad raíz. |
| Rutas alternativas máximas | Solo para ruta/patrón. Conserva rutas distintas hacia un mismo resultado; por defecto `3`. |
| Coincidencias máximas | Máximo de filas mostradas en cualquier modo; por defecto `10`. Se priorizan rutas cortas desde la raíz. |
| Filtro de metadata | Restringe resultados con información del metadata, sin consultar registros de SAP. |
| Coincidencia exacta | Para nombre, tipo, etiqueta y picklist, exige igualdad salvo que se utilicen comodines. |
| Incluir coincidencias globales | Incluye propiedades encontradas en todo el metadata, incluso sin una ruta desde la raíz. |
| Tabla de resultados | Muestra entidad, tipo, nivel, modo, ruta y estado de consulta. La columna Ruta tiene desplazamiento horizontal. |
| Consulta OData recomendada | Genera `$select` y `$expand` solo para las filas seleccionadas. |

## Modos de búsqueda

### Nombre de propiedad

Busca campos simples y `NavigationProperty` por nombre. Usa `*` para cualquier cantidad de caracteres y `?` para un carácter.

```text
custom_*
customTypeHire
*Posting?
```

`customTypeHire` puede ser una navegación hacia `PicklistOption` en `JobApplication` y una propiedad `Edm.Byte` en entidades de controles. La herramienta muestra ambas definiciones si son alcanzables desde la raíz.

### Tipo OData, sap:picklist y etiqueta sap:label

Localizan propiedades según el tipo, la lista de valores o la etiqueta declarada en metadata.

```text
Edm.DateTime
SFOData.PicklistOption
country
```

### Ruta / patrón OData

Busca una secuencia de navegaciones y una propiedad o navegación final. Las rutas que no comienzan con la raíz se tratan como parciales.

```text
rmk_region/externalCode
degreeNav/externalCode
*/jobRequisition/*/degreeNav/externalCode
*Nav/externalCode
custom_*
```

Reglas de comodines:

- Un segmento completo `*` representa cero o más directorios de navegación.
- Dentro de un nombre, `*` y `?` filtran caracteres: `*Nav` coincide con `degreeNav`; `custom_*` coincide con nombres que comienzan con `custom_`.

### Entidad relacionada y navegaciones de la raíz

El primer modo encuentra entidades alcanzables por nombre. El segundo lista las navegaciones directas de la entidad raíz.

## Filtro de metadata

El filtro se aplica después de la búsqueda principal y antes de limitar las coincidencias. No valida valores de registros ni ejecuta OData. Acepta condiciones con `AND` y `OR`; `AND` se evalúa antes que `OR`.

| Condición | Alias | Significado |
|---|---|---|
| `tipo:valor` | `type:valor` | Tipo del resultado, por ejemplo `Edm.Byte` o `SFOData.PicklistOption`. |
| `propiedad:valor` | `property:valor` | Nombre de la propiedad o navegación encontrada. |
| `destino:valor` | `destination:valor` | Entidad o EntitySet destino de una navegación. |
| `destino_propiedad:valor` | `destination_property:valor`, `propiedad_destino:valor` | Comprueba que la entidad destino tenga una propiedad con ese nombre. |

Ejemplos para `customTypeHire`:

```text
tipo:SFOData.PicklistOption
```

Muestra los casos donde es una navegación a una lista de valores.

```text
tipo:Edm.Byte
```

Muestra los controles definidos como campo simple.

```text
destino:PicklistOption AND destino_propiedad:externalCode
```

Muestra navegaciones cuyo destino es `PicklistOption` y permite obtener su código técnico `externalCode`.

```text
tipo:SFOData.PicklistOption OR tipo:Edm.Byte
```

Muestra cualquiera de las dos variantes. Los valores admiten `*` y `?`, por ejemplo `tipo:Edm.*` o `destino:*Picklist*`.

## Leer y copiar resultados

Haz clic sobre una celda para seleccionarla y usa `Ctrl+C` o clic derecho para copiar la celda, la ruta o la fila. La ruta completa queda disponible en la barra inferior y puede recorrerse con la barra horizontal de la tabla.

Al hacer doble clic se abre el inspector de detalles:

1. **Destino propiedades** y **Destino navegaciones** aparecen primero cuando el resultado es una navegación.
2. **Origen propiedades** muestra los campos directos de la entidad que contiene el resultado.
3. **Origen navegaciones** muestra las relaciones disponibles desde esa entidad.
4. El buscador interno filtra los cuatro listados. Sus tablas también permiten copiar celdas y filas.

Ejemplo: `degreeNav` puede llevar a `PicklistOption`; en **Destino propiedades** aparece `externalCode`, que es el código técnico usual para integrar una lista de valores.

## Consulta OData recomendada

Selecciona una o más filas de la tabla. La herramienta crea una URL con:

- `$select` para los campos requeridos.
- `$expand` para cada nivel de navegación necesario.

Por ejemplo, seleccionar la ruta `JobRequisitionPosting/jobRequisition/rmk_region/externalCode` genera expansiones para `jobRequisition` y `jobRequisition/rmk_region`.

La interfaz no incluye un `$filter` de datos porque trabaja únicamente con metadata. Si se requiere una expresión `$filter` manual, la versión de consola mantiene el argumento `--filter`; SAP solo la valida cuando se ejecuta la URL contra el servicio.

## Límites y prioridad de rutas

La profundidad define hasta dónde se puede navegar. No define cuántas rutas distintas se conservan.

Con ruta/patrón, el programa recorre primero las rutas de menor nivel mediante BFS. **Rutas alternativas máximas** determina cuántos caminos se retienen cuando varias rutas llegan al mismo estado del patrón. **Coincidencias máximas** se aplica al final, dejando primero las rutas más cortas desde la raíz y las coincidencias globales al final.

Una búsqueda amplia como `*Nav/externalCode` puede devolver muchas rutas válidas. Usa `degreeNav/externalCode`, menor profundidad o filtros de metadata para acotarla.

## Flujos sugeridos

### Obtener el código de un valor de lista

1. Selecciona la entidad raíz, por ejemplo `JobRequisitionPosting`.
2. Busca `degreeNav/externalCode` en **Ruta / patrón OData**.
3. Si hay varias rutas, usa `destino:PicklistOption AND destino_propiedad:externalCode`.
4. Selecciona la ruta correcta y copia la consulta recomendada.

### Distinguir un campo de control de una lista de valores

1. Busca `customTypeHire` con **Nombre de propiedad**.
2. Usa `tipo:SFOData.PicklistOption` para las navegaciones a lista.
3. Usa `tipo:Edm.Byte` para los campos de control.

### Encontrar campos personalizados

1. Busca `custom_*`.
2. Reduce la profundidad o escribe una ruta parcial, como `jobApplications/custom_*`.
3. Ajusta las rutas alternativas y coincidencias máximas si necesitas explorar más resultados.

## Seguridad y limitaciones

- No guardes credenciales en código ni en los XML de metadata.
- `$metadata` describe la estructura, no confirma que una entidad admita GET ni que existan registros.
- El valor de una picklist, los permisos, filtros de negocio y la disponibilidad de datos solo se validan al ejecutar una consulta real contra SAP.
