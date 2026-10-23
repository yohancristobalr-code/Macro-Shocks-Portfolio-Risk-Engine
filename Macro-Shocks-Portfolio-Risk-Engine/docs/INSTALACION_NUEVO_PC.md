# Instalar el motor en otro PC y hoja de ruta

Guía para llevar el Macro Shock Risk Engine a cualquier PC con Windows en unos 10 minutos, y plan para seguir haciéndolo crecer. **No necesita Claude ni ninguna IA**: todo se ejecuta en tu computadora con datos públicos.

## 1. Lo que necesitas

| Necesitas | Dónde |
|---|---|
| Python 3.11 o superior | [python.org/downloads](https://www.python.org/downloads/): marca **"Add python.exe to PATH"** al instalar |
| Git | [git-scm.com/download/win](https://git-scm.com/download/win) (opciones por defecto) |
| Clave gratuita de FRED | [fred.stlouisfed.org/docs/api/api_key.html](https://fred.stlouisfed.org/docs/api/api_key.html): mejor una **nueva** para cada PC |

## 2. Instalación (4 pasos)

En PowerShell, **un comando por línea**:

```powershell
cd $HOME
git clone -c core.autocrlf=false https://github.com/yohancristobalr-code/Macro-Shocks-Portfolio-Risk-Engine.git
cd Macro-Shocks-Portfolio-Risk-Engine\Macro-Shocks-Portfolio-Risk-Engine
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

El instalador:

1. Comprueba Python.
2. Crea un entorno propio (`.venv`) para no mezclar librerías con otros programas.
3. Instala el motor.
4. **Pide la clave de FRED** sin mostrarla y la guarda solo en `.env`, que nunca se sube a GitHub.
5. Ejecuta los tests y crea el acceso directo **"Informe Macro"** en el escritorio.

Puedes repetirlo sin riesgo: reutiliza lo que ya existe.

**Sin Git:** en GitHub, pulsa *Code → Download ZIP*, descomprímelo y ejecuta el paso 4 desde la carpeta interior. Para actualizar tendrás que repetir la descarga, así que Git es mejor.

## 3. Primer uso

Doble clic en **"Informe Macro"** del escritorio. En 1–3 minutos se abre en el navegador un informe HTML con:

| Sección | Qué responde |
|---|---|
| Visión sistemática | Sobreponderar / neutral / infraponderar por activo, los sectores preferidos y a evitar, y el calendario de 14 días |
| Lectura ejecutiva | Las conclusiones del día, generadas a partir de los números |
| 1. Qué descuenta el mercado | Escenario confirmado o invalidado y qué vigilar |
| 2. Crecimiento, inflación y cuadrante | Indicadores adelantados, pista del PMI y cuadrante macro |
| 3. Energía e IPC | Nowcast del IPC por subpartida, su validación y gráficas de precios |
| 4. Régimen de riesgo | Componentes de vulnerabilidad, VIX, crédito y bolsa |
| 5. Valoración de la bolsa | PER aproximado, indicador Buffett y prima de riesgo con su percentil histórico, y qué rindió la bolsa después |
| 6. Posicionamiento (CFTC) | Cuán largos o cortos están los especuladores en S&P 500, bonos, yen, petróleo, oro, cobre y Bitcoin |
| 7. Evidencia sectorial | Sectores y estilos con evidencia `**`/`*` en el régimen y en el cuadrante, y su convergencia |
| 8. Sectores del S&P 500 | Los 11 sectores frente al SPY, sus sensibilidades a la macro y el mapa de escenarios |
| 9. Metales | Oro, plata y cobre; el oro frente a la tasa real; los ratios oro/plata y cobre/oro |
| 10. Análogos y modelo | Los meses más parecidos desde 1990 y qué pasó después; modelo predictivo con su R² fuera de muestra |
| 11. Cripto | 10 monedas, apalancamiento (funding), sentimiento, stablecoins y régimen cripto |
| 12. Liquidez de la Fed | Liquidez neta, expectativas de tipos y su relación medida con el Nasdaq y BTC |
| 13. Carry del yen | Riesgo de desmontaje del carry trade y qué pasó en episodios anteriores |
| 14. Historial de aciertos | Cuánto acertaron las previsiones de informes anteriores |
| 15. Salud de los datos | Qué series están al día y cuáles fallaron |

Cada informe se guarda en `reports\informe_AAAA-MM-DD.html`, así que tendrás un historial que puedes comparar.

Desde PowerShell, con el entorno activado (`.\.venv\Scripts\Activate.ps1`), funcionan todos los comandos: `daily`, `macro`, `nowcast`, `sector-study`, `optimize`, `analyze`, `report`… Consulta [GUIA_USO.md](GUIA_USO.md).

## 4. Rutina recomendada

| Cuándo | Qué | Tiempo |
|---|---|---|
| **Antes de operar** | Doble clic en "Informe Macro", o `report --quick` si tienes prisa | 1–3 min |
| Cada día de mercado | `daily --quick` (se puede automatizar, ver [RUTINA_DIARIA.md](RUTINA_DIARIA.md), sección 6) | 1 min |
| Antes del IPC y del PMI | `nowcast` y `macro --quick` | 1 min |
| Cada mes | `optimize --max-drawdown <tu límite>` | 3 min |

La guía completa de interpretación está en [RUTINA_DIARIA.md](RUTINA_DIARIA.md).

## 5. Llevar tus datos personales al PC nuevo

Estos archivos **no están en GitHub** (por privacidad). Cópialos a mano, por ejemplo con una memoria USB, a la carpeta interior del PC nuevo:

| Archivo | Qué es |
|---|---|
| `portfolio.csv` | Tu cartera para las pruebas de estrés |
| `reports\` | Informes y el historial `daily_log.csv` |
| `.env` | **No lo copies**: crea una clave nueva para cada PC |

## 6. Actualizar

```powershell
cd $HOME\Macro-Shocks-Portfolio-Risk-Engine
git pull
cd Macro-Shocks-Portfolio-Risk-Engine
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

Volver a ejecutar el instalador reinstala las librerías nuevas y repite los tests: si todo sale `passed`, la actualización funciona.

## 7. Problemas frecuentes

| Síntoma | Solución |
|---|---|
| `Python 3.11+ not found` | Instala Python marcando "Add python.exe to PATH" y abre una PowerShell nueva |
| `ejecución de scripts deshabilitada` | Usa `powershell -ExecutionPolicy Bypass -File ...`, como en los ejemplos |
| `FRED_API_KEY is not set` | Borra `.env` y vuelve a ejecutar el instalador |
| Una sección del informe dice "no disponible" | Una fuente de datos falló ese día: el resto del informe es válido. Si persiste, ejecuta los tests (sección 6) |
| `Could not download ... ken.french` | La web de Dartmouth no responde: usa `report --quick` y reinténtalo más tarde |

## 8. Hoja de ruta: cómo seguir escalándolo

Por orden de valor. Cada paso es independiente y se puede construir con datos gratuitos:

1. **Escenarios con probabilidades**, calibradas con el historial de aciertos cuando tenga unos meses de datos.
2. **Validar el primer motor con comunicados reales**: pasar por `analyze` los textos de las reuniones de la Fed desde 2017 y medir si el puntaje de riesgo anticipa los movimientos del bono a 2 años, la bolsa y el VIX.
3. **Sorpresa de cada reunión de la Fed frente al mercado**: las expectativas (2 años − fed funds) ya están en el informe; falta usarlas en `analyze` para medir la sorpresa del anuncio.
4. **Completar el clasificador de riesgo** con divisas y materias primas, para pasar de 5 a 7 de los 8 grupos de datos.
5. **Caché local de datos**: informes más rápidos y que funcionen aunque FRED esté caído.
6. **Europa**: IPC armonizado y tipos del BCE desde el portal de datos del BCE, que es gratuito.
7. **Avisos automáticos**: enviar las alertas de `daily` por correo o Telegram, y programar el informe antes de la apertura.

Regla para cada mejora nueva: **tests con datos sintéticos de respuesta conocida, validación fuera de muestra y documentación en español**, como en todo lo construido hasta ahora.
