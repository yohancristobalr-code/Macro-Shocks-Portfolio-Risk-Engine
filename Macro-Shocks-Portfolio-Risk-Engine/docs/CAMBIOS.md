# Control de cambios

Las versiones se fijan en `pyproject.toml`. Cada cambio entra por un pull request a `main` con los tests de GitHub en verde.

## 1.2.0 — 23 de octubre de 2026

**PR #23: valoración, posicionamiento, calendario y documentación del proyecto**
- Valoración de la bolsa solo con FRED: PER aproximado, indicador Buffett y prima de riesgo, con su percentil y la rentabilidad posterior a 5 años.
- Posicionamiento de los especuladores (CFTC) para S&P 500, bono a 10 años, yen, petróleo, oro, cobre y Bitcoin, con alertas en los extremos.
- Calendario de los próximos 14 días (FRED y reuniones de la Fed) bajo la página 1.
- La valoración y el posicionamiento se suman a la visión por activo.
- Las secciones del informe se numeran solas.
- Nuevos documentos: `PROYECTO.md` (presentación, estado, fórmulas a revisar, hoja de ruta y contexto) y este control de cambios. Referencia rápida de comandos en `GUIA_USO.md`.

**PR #22: página 1, sectores del S&P 500, metales y estructura del VIX**
- Visión sistemática por activo (sobreponderar / neutral / infraponderar) y sectores preferidos y a evitar.
- Los 11 ETF de sectores frente al SPY (Stooq): fuerza relativa, sensibilidades medidas y mapa de escenarios.
- Oro, plata y cobre (Stooq, con los perpetuos XAU y XAG de Binance como respaldo): el oro frente a la tasa real y los ratios oro/plata y cobre/oro.
- VIX frente al VIX a 3 meses, con alerta en `daily`.

**PR #21: correcciones tras el primer informe con datos reales**
- Unidades de la liquidez de la Fed (la cuenta del Tesoro viene en millones).
- Formato de precios cripto, umbral de funding negativo y antigüedad de los datos en UTC.

## 1.1.x — 22–23 de octubre de 2026

- **PR #20:** carry trade del yen (señal de desmontaje y qué pasó después en episodios anteriores) e historial de aciertos (`reports/predicciones.csv`).
- **PR #19:** eje cripto (10 monedas, funding, interés abierto, Fear & Greed, stablecoins, régimen cripto y evidencia), liquidez de la Fed y salud de los datos. Corrección de los identificadores de la Fed de Nueva York.
- **PR #18 (versión 1.1.0):** informe ejecutivo HTML (`report`) con gráficas, análogos históricos y modelo predictivo validado fuera de muestra. Instalador `install.ps1`, acceso directo "Informe Macro" e `INSTALACION_NUEVO_PC.md`.
- **PR #17:** capa macro (`macro`): escenario que descuenta el mercado, indicadores adelantados de crecimiento, cuadrante crecimiento × inflación y sectores por cuadrante desde 1972.
- **PR #16:** nowcast del IPC por subpartidas energéticas (`nowcast`), bloque de energía y alertas en `daily`, y `analyze --inflation`.
- **PR #15:** régimen de crisis detectable con los índices de estrés de la Fed (STLFSI4 y NFCI); tests automáticos en GitHub Actions.

## 1.0.x — 19–22 de octubre de 2026

- **PR #4–#14:**
  - estudio FOMC de bitcoin, S&P 500 y VIX (`fomc-study`);
  - bitcoin en el árbol de escenarios (beta 1.0, colas 1.16);
  - prueba de estrés de la cartera propia (`--portfolio`);
  - biblioteca de French y sectores por régimen (`sector-study`) con robustez por mitades;
  - optimizador por riesgo con límite de caída (`optimize`);
  - rutina diaria (`daily`) con alertas y registro;
  - documentación en español (`GUIA_USO.md`, `RUTINA_DIARIA.md`);
  - carga de `.env` desde la raíz del proyecto.
- **PR #2–#3:** datos reales de FRED en `analyze` y alerta de suspensión ponderada por la probabilidad del escenario.
- **PR #1:** arreglos de instalación, carga de configuración, calendario de mercado y tests del proyecto original.

## 1.0.0 — abril de 2026

- Proyecto original (*fork*): análisis de comunicados de bancos centrales, puntaje de riesgo, árbol de escenarios, API REST y componentes en C++ y C#.
