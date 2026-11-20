# -*- coding: utf-8 -*-
"""
DIAGNOSTICADOR DE MERCADOS
Genera docs/index.html con tres pestañas: Diagnosticador · Opciones · Calendario.
Se ejecuta cada día desde GitHub Actions. Necesita el secreto TIINGO_API_KEY.
"""

import os
import re
import io
import json
import math
import time
import html
import datetime as dt

import numpy as np
import pandas as pd
import requests


# ----------------------------------------------------------------------------
# TEXTOS DE AYUDA (ⓘ)
# ----------------------------------------------------------------------------
# Textos de ayuda (ⓘ). Clave -> (título, párrafos/viñetas en HTML, [(fuente, url)])

FRED = "https://fred.stlouisfed.org/series/"

INFO = {
    # ---------------- DIAGNOSTICADOR: tarjetas y reglas ----------------
    "valoracion": ("Valoración de la bolsa de EE. UU. (ECY)", """
<p><b>Qué es.</b> El <i>Excess CAPE Yield</i> (ECY) de Shiller: la rentabilidad por beneficios ajustada al ciclo (1/CAPE) menos el tipo real del bono a 10 años. Mide cuánta prima ofrece la bolsa de EE. UU. frente a los bonos.</p>
<ul><li>&lt;1,5 %: muy cara · 1,5–3 %: cara · 3–4,5 %: razonable · ≥4,5 %: barata.</li>
<li>Una caída fuerte de la bolsa sube el ECY.</li></ul>
<p><b>Cómo se usa.</b> Solo afecta al S&amp;P 500 y al S&amp;P 500 equiponderado: para que sus condiciones sean "muy buenas" hace falta un ECY de al menos el 3 %.</p>
<p><b>Cuidado.</b> Mide valoración a 10 años; no sirve para elegir el momento a pocos meses. Para Europa, emergentes y MSCI World no hay una medida de valoración gratuita equivalente.</p>""",
        [("Shiller, Black y Jivraj (2020), CAPE and the COVID-19 pandemic effect", "https://spiral.imperial.ac.uk/handle/10044/1/86992"),
         ("Datos de Shiller", "https://shillerdata.com/"),
         ("AAII: ECY y rentabilidades a 10 años", "https://www.aaii.com/investor-update/article/235947-excess-cape-yield-low-ahead-of-potential-interest-rate-cut")]),

    "condiciones": ("Condiciones de indexación", """
<p>Valoración, para cada índice, de si es buen momento para tenerlo indexado. Se decide en este orden:</p>
<ul><li><b>Tendencia</b> (media de 40 semanas): alcista si el precio está por encima de su media y esta sube; bajista si está por debajo y baja; si no, lateral.</li>
<li><b>Muy malas:</b> tendencia bajista y, además, crédito deteriorándose con rapidez o precio todavía a más de un 10 % de su media de 200 semanas (le queda recorrido a la baja).</li>
<li><b>Malas:</b> tendencia alcista pero con señales de fin de ciclo (fase de madurez o giro, o 2 señales de agotamiento); o sin tendencia, con el impulso débil y lejos de la zona de suelo.</li>
<li><b>Regular-pesimista:</b> bajista pero ya cerca de la zona de suelo, sin señales suficientes; o sin tendencia y con el impulso débil.</li>
<li><b>Regular-optimista:</b> sin tendencia pero con el impulso mejorando; tendencia alcista pero perdiendo fuerza frente a la liquidez (fase de rezagado o suelo en el mapa); o bajista cerca del suelo con la puerta en tramo 2 o más.</li>
<li><b>Buenas:</b> tendencia alcista sana: fase de arranque o aceleración en el mapa y sin señales de fin de ciclo.</li>
<li><b>Muy buenas:</b> lo anterior y, además, precio atractivo: como mucho a un 10 % de su media de 200 semanas y, en los índices de EE. UU., ECY de al menos el 3 %.</li></ul>
<p><b>Plazo.</b> De 6 a 12 meses. Todo se calcula con cierres semanales: la tendencia con la media de 40 semanas (unos 9 meses), la fase del mapa con 26 semanas, la puerta con la media de 200 semanas (unos 4 años) y con una memoria de 13 semanas. No sirven para decidir a días o semanas: para eso está la pestaña Opciones.</p>
<p><b>Puerta:</b> puntos de suelo y tramo que corresponde construir. <b>Cuidado:</b> reglas de criterio propio, no calibradas.</p>""", []),

    "puntos": ("Puerta de indexación", """
<p><b>Qué es.</b> Suma de condiciones típicas de suelo de mercado, cumplidas en cualquier orden en las últimas 13 semanas. Se calcula para cada índice:</p>
<ul><li>Precio del índice cerca de su media de 200 semanas: 2 puntos.</li>
<li>Crédito girando a la baja desde su pico: 2 puntos (común a todos).</li>
<li>VIX vuelve a contango tras inversión: 1 punto (común a todos).</li>
<li>RSI semanal del índice en sobreventa: 1 punto.</li></ul>
<p>2, 4 y 6 puntos abren los tramos 1, 2 y 3: construir la posición en tercios. Si una condición deja de cumplirse no se abren tramos nuevos, pero no hay venta automática.</p>
<p><b>Cuidado.</b> Pesos y umbrales son de criterio propio, no calibrados: hay pocos mercados bajistas con datos.</p>""", []),

    "veto": ("Veto por crédito", """
<p><b>Qué es.</b> Freno de emergencia: si el diferencial de crédito (Baa − bono 10 años) sube un 30 % o más en 13 semanas y además acelera, el tramo máximo de la puerta queda en 1 y los índices en tendencia bajista pasan a condiciones "muy malas".</p>
<p><b>Por qué.</b> En 2001-02 y 2008-09 el S&amp;P 500 perforó su media de 200 semanas y siguió cayendo meses mientras el crédito se deterioraba. El veto evita comprar demasiado pronto en ese escenario.</p>
<p>Se levanta tras 4 semanas seguidas sin cumplirse.</p>
<p><b>En la tarjeta.</b> El diferencial actual en puntos (rentabilidad de los bonos de empresas Baa menos la del bono del Tesoro a 10 años) y su cambio en porcentaje en 13 semanas. Ejemplo: −13 % significa que el diferencial es un 13 % más estrecho que hace 13 semanas (por ejemplo, de 1,80 a 1,57 puntos): menos miedo al crédito. El veto exige +30 % o más.</p>""",
        [("Serie BAA10Y (FRED)", FRED + "BAA10Y")]),

    "conviccion": ("Grado de convicción", """
<p><b>Qué es.</b> Cuántos bloques independientes (régimen, estrés, liquidez y valoración/técnico/sentimiento) apuntan en la misma dirección que el mapa de la renta variable global.</p>
<ul><li>0–1 coincidencias: baja → no construir.</li><li>2–3: media → completar el tramo en 2–3 meses.</li><li>4: alta → completar el tramo en 1 mes.</li></ul>
<p><b>Cuidado.</b> Regla de criterio propio. A 6-12 meses ninguna señal tiene gran capacidad predictiva.</p>""", []),

    "c_ma200": ("Precio cerca de la media de 200 semanas", """
<p>La media de 200 semanas (unos 4 años) es una referencia de muy largo plazo. Históricamente los grandes suelos se han formado cerca de ella o por debajo.</p>
<p><b>En esta web.</b> Se cumple si el cierre semanal del índice está como mucho un 5 % por encima de su propia media de 200 semanas, o por debajo, en alguna de las últimas 13 semanas. Al lado se muestra la distancia actual.</p>
<p><b>Cuidado.</b> En 2001-02 y 2008-09 el precio la perforó y siguió cayendo meses (de ahí el veto por crédito).</p>""", []),

    "c_credito": ("Crédito girando a la baja desde su pico", """
<p>Cuando el diferencial de crédito empieza a bajar tras un episodio de estrés, suele indicar que lo peor de la crisis financiera ha pasado.</p>
<p><b>En esta web</b> (serie Baa − bono 10 años), deben cumplirse tres cosas: hubo estrés (máximo de 26 semanas ≥ 1,4 veces el mínimo del año anterior), el diferencial ha bajado un 15 % desde ese máximo y su media de 4 semanas está por debajo de la de 13.</p>""",
        [("Serie BAA10Y (FRED)", FRED + "BAA10Y")]),

    "c_vix": ("VIX vuelve a contango tras inversión", """
<p>Normalmente la volatilidad esperada a 3 meses (VIX3M) es mayor que la de 1 mes (VIX): eso es "contango". En pánicos se invierte (VIX &gt; VIX3M). La vuelta a la normalidad suele acompañar el final de la fase aguda de miedo.</p>
<p><b>En esta web.</b> Al menos una semana con VIX/VIX3M &gt; 1 y después dos cierres semanales seguidos por debajo de 0,95.</p>""",
        [("VIX3M (Macroption)", "https://www.macroption.com/vix3m/")]),

    "c_rsi": ("RSI semanal en sobreventa", """
<p>El RSI (índice de fuerza relativa, J. Welles Wilder, 1978) compara subidas y bajadas recientes en una escala de 0 a 100. Por debajo de 30 se considera sobreventa.</p>
<p><b>En esta web.</b> RSI de 14 semanas de cada índice por debajo de 30 en alguna de las últimas 13 semanas. En datos semanales es una señal rara, típica de caídas fuertes.</p>""", []),

    "b_A": ("Bloque A · Régimen", """
<p>Dirección de la economía. Vota a favor del riesgo si las encuestas industriales de la Fed (Filadelfia y Nueva York) mejoran a 3 meses y las peticiones de subsidio bajan. Vota en contra si la regla de Sahm llega a 0,5 o si ambas empeoran.</p>""", []),
    "b_B": ("Bloque B · Estrés", """
<p>Tensión financiera. A favor del riesgo si el diferencial de crédito baja a 13 semanas y el VIX está en contango. En contra si el diferencial sube y el VIX está invertido.</p>""", []),
    "b_C": ("Bloque C · Liquidez", """
<p>A favor del riesgo si la liquidez neta de la Fed sube a 13 semanas y las condiciones financieras (NFCI) se relajan. En contra si ocurre lo contrario.</p>
<p><b>Cuidado.</b> La relación liquidez-bolsa es una idea muy popular desde 2020, con poca evidencia académica sólida.</p>""", []),
    "b_EFG": ("Bloque E+F+G · Valoración, técnico y sentimiento", """
<p>Usa el indicador de sentimiento (réplica del Euphoriameter). Por encima de 80 vota en contra del riesgo (euforia); por debajo de 20, a favor (miedo).</p>""", []),
    "b_D": ("Bloque D · Beta o alfa", """
<p>No vota. Indica si el entorno favorece seleccionar valores o comprar el índice.</p>
<ul><li>Dispersión alta (DSPX) y correlación baja (COR3M): los valores se mueven distinto entre sí → más margen para la selección (alfa).</li>
<li>Lo contrario: todo se mueve junto → el índice (beta) es más eficiente.</li></ul>
<p><b>Umbrales.</b> Favorable a selección si la dispersión está por encima de su percentil 60 y la correlación por debajo del 40; al revés, favorable al índice. Percentiles sobre toda su historia: DSPX desde 2014 y COR3M desde 2006.</p>
<p><b>Cuidado.</b> Es el bloque con evidencia más débil: los informes SPIVA muestran que la mayoría de gestores activos no bate al índice a largo plazo.</p>""",
        [("Cboe: correlación implícita", "https://cboe.com/us/indices/implied/"),
         ("Cboe: índice de dispersión", "https://www.cboe.com/us/indices/dispersion")]),
    "sentimiento": ("Sentimiento (réplica del Euphoriameter)", """
<p>Media de tres percentiles, cada uno sobre toda su historia disponible: valoración (CAPE, desde 1881), calma (VIX invertido, desde 1990) y distancia del ACWI a su media de 200 semanas (desde que hay 200 semanas de datos, 2012). De 0 (miedo) a 100 (euforia).</p>
<p><b>Por qué una réplica.</b> El Euphoriameter original no tiene datos descargables, y el índice NAAIM, que iba a usarse, pasó a ser de pago.</p>
<p><b>Cuidado.</b> Sus componentes se solapan con la valoración y el técnico: cuenta como un único input.</p>""", []),

    # ---------------- Mapas ----------------
    "mapa": ("Mapa de rotación de motores", """
<p>Versión abierta y aproximada de los <i>Relative Rotation Graphs</i> de Julius de Kempenaer. Cada punto es un motor de rentabilidad, comparado con las letras del Tesoro (BIL).</p>
<ul><li><b>Eje horizontal (fuerza):</b> 100 = su comportamiento medio frente a la liquidez en los últimos 6 meses. 103 significa un 3 % mejor que esa media; 97, un 3 % peor.</li>
<li><b>Eje vertical (impulso):</b> 100 = la fuerza no ha cambiado en 4 semanas. 102 significa que ha mejorado un 2 % en ese mes; 98, que ha empeorado un 2 %.</li>
<li>La escala se ajusta a los motores que tengas marcados. Por defecto solo aparece el S&amp;P 500 (SPY).</li>
<li>El giro habitual es en sentido horario: mejorando → liderando → debilitándose → rezagado.</li>
<li>La cola muestra las últimas 24 semanas (cierres semanales): el cuadrado es el punto de partida y el círculo grande, el momento actual.</li>
<li>Los colores indican la familia del motor (renta variable, renta fija, materias primas…), no su fase. Puedes ocultar familias o motores con las casillas.</li>
<li><b>Pulsa sobre un motor</b> (o elígelo en la lista de debajo) para leer su recorrido y su tendencia explicados con palabras.</li>
<li>El selector de vista cambia el mapa por una gráfica en base 100 que compara la evolución de los motores entre sí y con las letras del Tesoro.</li></ul>
<p><b>Cuidado.</b> Los proxies son fondos cotizados en dólares; no reproduce exactamente el RRG comercial (es una marca registrada).</p>""",
        [("StockCharts ChartSchool: RRG", "https://chartschool.stockcharts.com/table-of-contents/chart-analysis/chart-types/relative-rotation-graphs-rrg-charts"),
         ("StockCharts: patrón de rotación", "https://c.stockcharts.com/articles/chartwatchers/2019/04/when-tails-remain-on-one-side-of-a-relative-rotation-graph-what-does-that-mean.html")]),
    "base100": ("Evolución en base 100", """
<p>Cada línea muestra en cuánto se ha convertido lo que valía 100 al inicio del periodo elegido, con los dividendos reinvertidos. Todas parten del mismo punto, así que se comparan directamente.</p>
<ul><li><b>Línea negra discontinua:</b> las letras del Tesoro (BIL), la referencia de liquidez. Lo que queda por debajo lo ha hecho peor que tener el dinero en liquidez.</li>
<li><b>Escala logarítmica:</b> la misma distancia vertical equivale al mismo porcentaje de subida o bajada, esté la línea arriba o abajo.</li>
<li>Debajo, la rentabilidad de cada motor en el periodo, de mayor a menor.</li>
<li>Pulsa sobre una línea para leer la dinámica de ese motor en el mapa de rotación.</li></ul>
<p><b>Cuidado.</b> Todo en dólares: para un inversor en euros, el efecto divisa se ve con FXE (euro).</p>""", []),
    "fuerza": ("Fuerza relativa", """
<p>Compara el motor con las letras del Tesoro (liquidez en dólares): se divide su precio entre el de las letras y se mira si esa relación está por encima o por debajo de su media de 26 semanas.</p>
<p><b>Lectura.</b> Por encima de 100, el motor está batiendo a la liquidez en su tendencia reciente; por debajo de 100, lo hace peor que tener el dinero en liquidez.</p>""", []),
    "impulso": ("Impulso", """
<p>Velocidad a la que cambia la fuerza relativa: compara la fuerza actual con la de hace 4 semanas.</p>
<p><b>Lectura.</b> Por encima de 100, la fuerza está mejorando (acelera); por debajo, empeorando (frena). El impulso suele girar antes que la fuerza: por eso anticipa las rotaciones.</p>""", []),
    "agotamiento": ("Señales de agotamiento", """
<p>Cuántas de estas cuatro señales de extremo se dan ahora (de 0 a 4):</p>
<ul><li>RSI semanal por encima de 70 (alcista) o por debajo de 30 (bajista).</li>
<li>Distancia a su media de 40 semanas entre el 10 % más alto (alcista) o más bajo (bajista) de toda la historia del fondo.</li>
<li>Fuerza frente a la liquidez por encima de su percentil 90 (alcista) o por debajo del 10 (bajista), sobre toda la historia del fondo.</li>
<li><b>Divergencia:</b> el precio está en la zona de máximos de 26 semanas (a menos de un 2 %) pero el RSI ya está 10 puntos o más por debajo de su máximo de esas 26 semanas: sube, pero con menos fuerza. Simétrico en mínimos para la bajista.</li></ul>
<p><b>Lectura.</b> "Alcista 2 de 4" indica una subida muy estirada: riesgo de fin de ciclo. "Bajista 2 de 4" indica una caída muy estirada: posible suelo. Con 2 o más señales, la fase pasa a madurez (o suelo).</p>
<p><b>Cuidado.</b> Umbrales de criterio propio, no calibrados. La divergencia es una señal técnica clásica con evidencia académica débil.</p>""", []),
    "direccion": ("Dirección del mapa (RV global)", """
<p>Fase en la que está el ACWI (bolsa mundial) en el mapa de rotación. Suelo, arranque o aceleración cuentan como dirección favorable al riesgo; madurez, giro o rezagado, como desfavorable. La convicción mide cuántos bloques coinciden con esta dirección.</p>""", []),
    "top_salir": ("Cerca de fin de ciclo · ir saliendo", """
<p>Motores en fase de <b>giro</b> (fuerza alta pero impulso ya por debajo de 100) o de <b>madurez</b> (impulso frenando 3 semanas o 2 señales de agotamiento alcista).</p>
<p>Orden: primero los que están en giro y, dentro de cada fase, los que más impulso han perdido en las últimas 4 semanas.</p>
<p>No incluye volatilidad (VIXY), managed futures (DBMF) ni anti-beta (BTAL): son coberturas o estrategias, no motores que se roten por tendencia. Siguen en el mapa y en la tabla como referencia.</p>""", []),
    "top_ampliar": ("Tendencia alcista con recorrido · ampliar", """
<p>Motores en fase de <b>aceleración</b> (fuerza e impulso por encima de 100, sin frenar) y con como mucho una señal de agotamiento alcista: la tendencia está viva y no parece estirada.</p>
<p>Orden: de mayor a menor impulso. No incluye volatilidad, managed futures ni anti-beta.</p>""", []),
    "top_construir": ("Podrían iniciar tendencia alcista · empezar a construir", """
<p>Motores en fase de <b>arranque</b>: todavía lo hacen peor que la liquidez (fuerza por debajo de 100), pero su impulso ya es positivo. Es donde está el margen: al principio del ciclo.</p>
<p>Orden: de mayor a menor impulso. Recuerda construir poco a poco: muchos arranques fallan. No incluye volatilidad, managed futures ni anti-beta.</p>""", []),
    "nivel_macro": ("Nivel (percentil histórico)", """
<p>Qué parte de <b>toda la historia</b> de la variable queda por debajo de su valor actual. 0 = mínimo histórico, 100 = máximo histórico, 50 = su valor típico (mediana).</p>
<p>Cada variable empieza en un año distinto: la tabla de variables indica desde cuándo. El rastro del mapa solo enseña por dónde ha pasado en las últimas 24 semanas, pero cada punto se compara siempre con toda la historia.</p>""", []),
    "impulso_macro": ("Impulso de una variable macro", """
<p>Cambio de la variable en las últimas 13 semanas (datos semanales y diarios) o en los últimos 6 meses (datos mensuales). En el mapa se expresa como percentil de ese cambio sobre <b>todos los cambios de su historia</b>: por encima de 50, la variable sube más deprisa de lo habitual; por debajo, baja o sube menos de lo habitual.</p>""", []),
    "fases": ("Fases y qué hacer", """
<ul><li><b>Suelo:</b> fuerza e impulso bajos, pero el impulso sube 3 semanas o hay agotamiento bajista → vigilar.</li>
<li><b>Arranque:</b> fuerza baja pero impulso al alza → construir poco a poco (el margen está al principio).</li>
<li><b>Aceleración:</b> fuerza e impulso altos → mantener.</li>
<li><b>Madurez:</b> fuerza alta pero impulso frenando 3 semanas, o 2 señales de agotamiento → empezar a salir antes del clímax.</li>
<li><b>Giro:</b> fuerza alta e impulso bajo → salir.</li>
<li><b>Rezagado:</b> fuerza e impulso bajos sin señal de suelo → no tocar.</li></ul>
<p><b>Agotamiento (↑/↓):</b> número de señales de extremo alcista/bajista (de 4): RSI semanal &gt;70 o &lt;30; distancia a la media de 40 semanas en el 10 % extremo de la historia del fondo; fuerza relativa en el percentil &gt;90 o &lt;10 de la historia del fondo; divergencia entre precio y RSI en 26 semanas.</p>""", []),
    "mapa_macro": ("Mapa macro", """
<p>Sitúa cada variable por su <b>nivel</b> (percentil histórico, eje horizontal) y su <b>impulso</b> (percentil de su cambio reciente, eje vertical). Sirve para ver en qué fase está cada pata del régimen: si está alta y todavía subiendo, alta y frenando, etc.</p>
<p>Una variable alta y subiendo puede estar cerca de su clímax: el margen está en anticipar el giro, no en seguirla.</p>
<p><b>Ejes de 0 a 100.</b> Son percentiles sobre toda la historia de cada variable (no sobre las últimas 24 semanas): el rastro solo enseña el camino reciente.</p>
<p><b>Pulsa sobre una variable</b> (o elígela en la lista de debajo) para leer qué significa para el mercado.</p>""", []),
    "regimen": ("Régimen macro", """
<p>Combina la <b>dirección del crecimiento</b> (encuestas industriales de la Fed) y la <b>dirección de la inflación</b> (IPC interanual) en cuatro entornos, siguiendo el «reloj de inversión» de Merrill Lynch:</p>
<ul><li><b>Recuperación</b> (crecimiento ↑, inflación ↓): históricamente lo mejor para la bolsa.</li>
<li><b>Sobrecalentamiento</b> (↑, ↑): materias primas y energía; sufren los bonos. Las buenas noticias pueden ser malas porque alejan las bajadas de tipos.</li>
<li><b>Estanflación</b> (↓, ↑): lo peor para bolsa y bonos; aguantan liquidez, materias primas, oro y estrategias de tendencia.</li>
<li><b>Desaceleración</b> (↓, ↓): bonos del Tesoro. Los datos flojos pueden ser buenos para la bolsa mientras no haya recesión.</li></ul>
<p><b>Por qué las noticias buenas pueden ser malas.</b> Un dato económico mezcla dos mensajes: beneficios futuros y tipos de interés futuros. En expansión pesa más el de los tipos, así que un dato flojo suele subir la bolsa; en recesión pesa más el de los beneficios y ocurre lo contrario (Boyd, Hu y Jagannathan, 2005). Con la inflación por encima del objetivo, un dato fuerte se lee como «más tipos».</p>
<p><b>Cuidado.</b> Son tendencias históricas, no leyes. Aquí el crecimiento se aproxima con encuestas regionales de la Fed y se usa la dirección (cambio a 6 meses), no el nivel frente a su tendencia como en el estudio original. El umbral del 3 % de inflación es de criterio propio.</p>""",
        [("Merrill Lynch (2004): The Investment Clock (Greetham y Hartnett)", "https://ks3-cn-beijing.ksyun.com/attachment/114caaf9991619a15e81a0a4b590837f"),
         ("Boyd, Hu y Jagannathan (2005), Journal of Finance", "https://doi.org/10.1111/j.1540-6261.2005.00742.x"),
         ("Neville, Draaisma, Funnell, Harvey y Van Hemert (2021), The Best Strategies for Inflationary Times", "https://ssrn.com/abstract=3813202")]),
    "variables": ("Variables", """
<p><b>Percentil histórico:</b> qué parte de toda su historia, desde el año indicado, queda por debajo del valor actual (100 = máximo histórico).</p>
<p><b>Cambio reciente:</b> 13 semanas para los datos rápidos y 6 meses para los mensuales.</p>
<p><b>Fase:</b> misma lógica que los motores, aplicada a nivel e impulso. "Madurez" = nivel alto (percentil &gt;80) con el impulso frenando.</p>
<p>⚠ = el dato lleva más tiempo del normal sin actualizarse.</p>""", []),

    # ---------------- Variables macro ----------------
    "Inflación (IPC interanual, %)": ("Inflación (IPC interanual)", """
<p>Variación anual del índice de precios al consumo de EE. UU. (todos los artículos, desestacionalizado). Mensual, con unas 2 semanas de retraso.</p>
<p><b>Lectura.</b> Lo relevante no es el nivel sino la dirección: inflación acelerando favorece activos reales (materias primas, energía); frenando favorece bonos y crecimiento.</p>""",
        [("Serie CPIAUCSL (FRED)", FRED + "CPIAUCSL")]),
    "Crecimiento (encuestas Fed, media 3 meses)": ("Crecimiento (encuestas industriales de la Fed)", """
<p>Media de las encuestas manufactureras de la Fed de Filadelfia y la de Nueva York (Empire State), suavizada a 3 meses. Son índices de difusión: por encima de 0, más empresas ven mejora que empeoramiento.</p>
<p><b>Por qué estas.</b> Sustituyen al ISM manufacturero, que no está disponible en descarga gratuita.</p>""",
        [("Fed Filadelfia (FRED)", "https://fred.stlouisfed.org/data/GACDFSA066MSFRBPHI"),
         ("Fed Nueva York (FRED)", FRED + "GACDISA066MSFRBNY")]),
    "Estrés de crédito: diferencial Baa − Tesoro 10 años (puntos)": ("Diferencial de crédito Baa − Tesoro a 10 años", """
<p><b>Qué es.</b> La diferencia entre lo que rinden los bonos de empresas con calificación <b>Baa</b> de Moody's y lo que rinde el <b>bono del Tesoro de EE. UU. a 10 años</b> (no la letra a corto plazo).</p>
<ul><li><b>Baa</b> es el escalón más bajo del grado de inversión: empresas solventes, pero justo por encima del <i>high yield</i>.</li>
<li>Se mide en <b>puntos porcentuales</b>: 1,6 significa que una empresa Baa paga un 1,6 % anual más que el Tesoro por pedir prestado a plazo parecido.</li>
<li>Diario desde 1986 (aquí, cierres semanales).</li></ul>
<p><b>Lectura.</b> Sube = el mercado exige más por prestar a empresas: miedo a impagos, suele anticipar o acompañar caídas de la bolsa. Baja = confianza. Muy bajo = complacencia. Es la serie de crédito que usan las reglas porque permite comparar con 2002, 2008, 2020 y 2022.</p>""",
        [("Serie BAA10Y (FRED)", FRED + "BAA10Y")]),
    "Liquidez neta de la Fed (billones $)": ("Liquidez neta de la Fed", """
<p>Balance de la Fed menos la cuenta del Tesoro en la Fed menos el repo inverso. Aproxima la liquidez que la Fed deja en el sistema financiero.</p>
<p><b>Cuidado.</b> Es una heurística popular entre analistas desde 2020; la evidencia académica de su relación con la bolsa es escasa.</p>""",
        [("Balance WALCL (FRED)", FRED + "WALCL"), ("Cuenta del Tesoro WTREGEN (FRED)", FRED + "WTREGEN"),
         ("Repo inverso RRPONTSYD (FRED)", FRED + "RRPONTSYD")]),
    "Condiciones financieras (Chicago Fed)": ("Condiciones financieras (NFCI, Chicago Fed)", """
<p>Índice semanal que resume 105 medidas de riesgo, crédito y apalancamiento. Cero es la media histórica desde 1971.</p>
<p><b>Lectura.</b> Positivo = condiciones más tensas que la media; negativo = más holgadas que la media.</p>""",
        [("Chicago Fed: sobre el NFCI", "https://www.chicagofed.org/research/data/nfci/about")]),
    "Estrés financiero (St. Louis Fed)": ("Estrés financiero (St. Louis Fed)", """
<p>Índice semanal construido con 18 series de tipos de interés, diferenciales y otros indicadores. Cero = estrés normal.</p>
<p><b>Lectura.</b> Positivo = estrés por encima de la media; negativo = por debajo.</p>""",
        [("Serie STLFSI4 (FRED)", FRED + "STLFSI4")]),
    "Curva 10 años − 3 meses": ("Curva de tipos 10 años − 3 meses", """
<p>Diferencia entre el bono del Tesoro a 10 años y la letra a 3 meses.</p>
<p><b>Lectura.</b> Cuando se invierte (negativa) ha precedido a las recesiones de EE. UU. desde los años 50, con un año o dos de adelanto e imprecisión en el momento. Según la Fed de San Francisco, esta versión de la curva es la de mejor historial.</p>
""",
        [("Fed de San Francisco (2022)", "https://www.frbsf.org/research-and-insights/publications/economic-letter/current-recession-risk-according-to-yield-curve"),
         ("Serie T10Y3M (FRED)", FRED + "T10Y3M")]),
    "Tipo real 10 años (TIPS)": ("Tipo real a 10 años (TIPS)", """
<p>Rentabilidad del bono del Tesoro a 10 años protegido contra la inflación: el coste real del dinero a largo plazo.</p>
<p><b>Lectura.</b> Tipos reales altos y subiendo presionan a la baja la valoración de bolsa, oro y activos de larga duración; bajando, los favorecen.</p>""",
        [("Serie DFII10 (FRED)", FRED + "DFII10")]),
    "Inflación implícita 10 años": ("Inflación implícita a 10 años", """
<p>Diferencia entre el bono nominal a 10 años y el ligado a la inflación: la inflación media que descuenta el mercado para la próxima década.</p>
<p><b>Lectura.</b> Subiendo = el mercado teme más inflación (favorece activos reales); bajando = teme desinflación o recesión.</p>""",
        [("Serie T10YIE (FRED)", FRED + "T10YIE")]),
    "Dólar (índice amplio)": ("Dólar (índice amplio)", """
<p>Valor del dólar frente a una cesta amplia de divisas de sus socios comerciales.</p>
<p><b>Lectura.</b> Un dólar fuerte endurece las condiciones financieras globales y suele perjudicar a emergentes y materias primas. Para un inversor en euros, además, cambia el valor de todo lo que está en dólares.</p>""",
        [("Serie DTWEXBGS (FRED)", FRED + "DTWEXBGS")]),
    "Peticiones de subsidio (media 4 semanas)": ("Peticiones iniciales de subsidio por desempleo", """
<p>Nuevas solicitudes semanales de prestación por desempleo en EE. UU. (media de 4 semanas). Es de los datos de empleo más rápidos.</p>
<p><b>Lectura.</b> Subidas sostenidas suelen anticipar deterioro del mercado laboral. Ojo: el percentil compara con una historia desde 1967, con una población mucho menor.</p>""",
        [("Serie ICSA (FRED)", FRED + "ICSA")]),
    "Regla de Sahm": ("Regla de Sahm", """
<p>Compara la media de 3 meses del paro con su mínimo de los 12 meses anteriores. Si la diferencia llega a 0,50 puntos, marca el inicio de una recesión.</p>
<p><b>Lectura.</b> Es una señal de confirmación temprana, no de anticipación: salta cuando la recesión ya ha empezado.</p>""",
        [("Serie SAHMREALTIME (FRED)", FRED + "SAHMREALTIME")]),
    "Condiciones financieras FCI-G (Fed)": ("Condiciones financieras FCI-G (Reserva Federal)", """
<p>Índice de la Reserva Federal que traduce siete variables (tipos oficiales, bono a 10 años, hipotecas, bonos corporativos BBB, bolsa, vivienda y dólar) en su efecto sobre el PIB del año siguiente.</p>
<p><b>Lectura.</b> Positivo = viento en contra del crecimiento; negativo = viento a favor. Un valor de 1 equivale a restar (o sumar) un punto de crecimiento. Es mensual y se publica con retraso.</p>""",
        [("Reserva Federal (Ajello y otros, 2023)", "https://www.federalreserve.gov/econres/notes/feds-notes/a-new-index-to-measure-us-financial-conditions-20230630.html")]),
    "Cobre / oro": ("Ratio cobre / oro", """
<p>Precio del cobre (metal industrial, sensible al crecimiento) frente al oro (refugio).</p>
<p><b>Lectura.</b> Subiendo suele acompañar expectativas de más crecimiento y tipos más altos; bajando, de menos crecimiento o miedo.</p>
<p><b>Cuidado.</b> Es una heurística de mercado sin respaldo académico sólido. Aquí se mide con dos fondos cotizados (CPER y GLD), con historia desde 2011.</p>""", []),
    "Estructura del VIX (VIX/VIX3M)": ("Estructura del VIX (VIX/VIX3M)", """
<p>Volatilidad esperada a 1 mes frente a la de 3 meses.</p>
<p><b>Lectura.</b> Por debajo de 1 (contango) es lo normal. Por encima de 1 (inversión) indica miedo agudo a corto plazo. La vuelta por debajo de 0,95 tras una inversión cuenta como punto en la puerta.</p>""",
        [("VIX3M (Macroption)", "https://www.macroption.com/vix3m/")]),
    "Dispersión (DSPX)": ("Dispersión (Cboe DSPX)", """
<p>Mide cuánto se espera que difieran entre sí los valores del S&amp;P 500 en los próximos 30 días, a partir de opciones del índice y de opciones de sus valores. Lo publican Cboe y S&amp;P DJI desde 2023, con historia retrocalculada desde 2014.</p>
<p><b>Lectura.</b> Alta = cada valor va por su lado, más margen para la selección. Baja = todo se mueve junto.</p>""",
        [("Cboe: índice de dispersión", "https://www.cboe.com/us/indices/dispersion"),
         ("S&P DJI: preguntas frecuentes", "https://spglobal.com/spdji/en/education/article/faq-cboe-sp-500-dispersion-index")]),
    "Correlación implícita (COR3M)": ("Correlación implícita a 3 meses (Cboe COR3M)", """
<p>Correlación media esperada entre los 50 mayores valores del S&amp;P 500 en los próximos 3 meses, deducida de sus opciones. Cboe la describe como una medida del "comportamiento en manada".</p>
<p><b>Lectura.</b> Alta = la diversificación entre acciones aporta poco y manda el índice; baja = beneficios de diversificación altos y más margen para la selección.</p>""",
        [("Cboe: correlación implícita", "https://cboe.com/us/indices/implied/"),
         ("Cboe: documento técnico", "https://cdn.cboe.com/resources/indices/documents/Implied_Correlation-WhitePaper-v1.0.5.pdf")]),
    "Diferencial high yield (solo 3 años)": ("Diferencial high yield", """
<p>Prima que pagan los bonos corporativos de alto rendimiento sobre el Tesoro (índice de ICE BofA).</p>
<p><b>Lectura.</b> Es el termómetro de crédito más sensible al estrés.</p>
<p><b>Cuidado.</b> FRED solo ofrece sus 3 últimos años, así que su percentil no es histórico: se muestra como alarma complementaria y no entra en las reglas.</p>""",
        [("Serie BAMLH0A0HYM2 (FRED)", FRED + "BAMLH0A0HYM2")]),
    "Equiponderado / capitalización (RSP/SPY)": ("Equiponderado frente a capitalización (RSP/SPY)", """
<p>S&amp;P 500 con todas las empresas al mismo peso frente al S&amp;P 500 normal, ponderado por tamaño.</p>
<p><b>Lectura.</b> Subiendo = la subida se extiende a más empresas (amplitud sana). Bajando = mandan unas pocas grandes (concentración). Sustituye a las medidas de amplitud y concentración, que no tienen fuente gratuita.</p>""", []),

    # ---------------- OPCIONES ----------------
    "gamma": ("Gamma de los creadores de mercado (aproximada)", """
<p><b>Qué es.</b> Estimación de cómo tendrán que cubrirse los creadores de mercado de opciones del S&amp;P 500 cuando el índice se mueva.</p>
<ul><li><b>Positiva:</b> compran en las caídas y venden en las subidas → amortiguan los movimientos.</li>
<li><b>Negativa:</b> venden en las caídas y compran en las subidas → amplifican los movimientos.</li></ul>
<p><b>Cuidado.</b> Se basa en un supuesto simplificado y estándar (los creadores tienen las calls compradas y las puts vendidas). No predice la dirección del mercado, solo cómo de nervioso puede estar. Excluye las opciones que vencen el mismo día.</p>""",
        [("SpotGamma: qué es la exposición gamma", "https://spotgamma.com/gamma-exposure-explained/")]),
    "giro": ("Nivel de giro de la gamma", """
<p>Nivel del S&amp;P 500 en el que la gamma estimada cambia de signo.</p>
<p><b>Lectura.</b> Por encima del giro, el mercado tiende a estar amortiguado; por debajo, los movimientos tienden a amplificarse.</p>""", []),
    "muros": ("Muros de opciones", """
<p>Precios de ejercicio con más interés abierto en los próximos 70 días: el de puts por debajo del precio actual y el de calls por encima.</p>
<p><b>Lectura.</b> Suelen actuar como soporte (put) y resistencia (call) a corto plazo. Pierden fuerza tras los vencimientos.</p>""", []),
    "skew": ("SKEW (Cboe)", """
<p><b>Qué es.</b> Mide el precio de las opciones put muy fuera de dinero sobre el S&amp;P 500 frente a las opciones en dinero, a 30 días. Con rentabilidades "normales" valdría 100; sube cuanto más se paga por protegerse de caídas extremas. Suele moverse entre 100 y 150.</p>
<ul><li><b>Alto (más de 140):</b> los grandes inversores pagan mucho por cubrirse del riesgo de cola. Proteger la cartera con puts lejanas sale caro; venderlas paga más prima, a cambio de asumir ese riesgo.</li>
<li><b>No es una señal de caída inminente.</b> Mide el precio de la protección, no la probabilidad real de un desplome. Según algunos analistas, suele estar alto en mercados tranquilos, cuando cubrirse resulta barato en términos absolutos y los fondos compran protección de forma sistemática.</li></ul>
<p><b>Percentiles.</b> El histórico compara con toda su historia desde 1990; el anual, con el último año. El nivel del SKEW ha tendido a subir en las últimas décadas, así que el percentil anual es más útil para ver si está extremo ahora.</p>""",
        [("Cboe: metodología del SKEW", "https://cdn.cboe.com/resources/release_notes/2025/Consultation-Regarding-Proposed-Changes-to-the-Cboe-SKEW-Index.pdf"),
         ("Cboe: origen del skew", "https://www.cboe.com/insights/posts/dawn-of-a-new-era-brings-on-the-existence-of-skew")]),
    "putcall": ("Ratio put/call", """
<p><b>Qué es.</b> Volumen diario de opciones put (protección) dividido por el de calls (apuestas alcistas) en Cboe.</p>
<ul><li><b>Acciones:</b> refleja sobre todo al inversor particular; es el más usado como señal.</li>
<li><b>Índices y S&amp;P 500:</b> refleja más las coberturas institucionales; suele ser más alto que el de acciones.</li>
<li><b>Total:</b> la suma de ambos.</li></ul>
<p><b>Lectura.</b> Se usa como indicador contrario: extremos altos (mucho miedo) suelen coincidir con zonas de suelo y extremos bajos (complacencia), con zonas de techo. Mejor con la media de 10 días que con el dato de un día.</p>
<p><b>Cuidado.</b> No sirve para afinar fechas exactas.</p>""",
        [("StockCharts ChartSchool: ratio put/call", "https://chartschool.stockcharts.com/table-of-contents/market-indicators/put-call-ratio"),
         ("AAII: el ratio put/call", "https://www.aaii.com/journal/article/295384-the-put-call-ratio-viewing-market-sentiment-through-options-activity")]),
    "cot": ("Posicionamiento (CFTC)", """
<p><b>Qué es.</b> Informe semanal del regulador de futuros de EE. UU. con las posiciones de los grandes operadores (datos del martes, publicados el viernes).</p>
<ul><li><b>Gestoras:</b> fondos de inversión, pensiones y aseguradoras. Posicionamiento lento y estructural.</li>
<li><b>Fondos apalancados:</b> hedge funds y fondos de gestión tendencial (CTA). Más tácticos y rápidos.</li>
<li><b>Especuladores</b> (materias primas): gestores de dinero (fondos y CTA) que operan por cuenta de terceros.</li></ul>
<p><b>Lectura.</b> Neto = largos menos cortos, en % del interés abierto. Percentiles extremos (≥90 o ≤10 en 3 años) señalan posiciones muy cargadas, que pueden deshacerse con violencia si el mercado se gira.</p>""",
        [("CFTC: notas del informe financiero", "https://www.cftc.gov/idc/groups/public/@commitmentsoftraders/documents/file/tfmexplanatorynotes.pdf"),
         ("CFTC: notas del informe de materias primas", "https://www.cftc.gov/MarketReports/CommitmentsofTraders/DisaggregatedExplanatoryNotes/index.htm"),
         ("OFR: quiénes son los fondos apalancados", "https://financialresearch.gov/hedge-fund-monitor/categories/size/chart-94")]),

    # ---------------- CALENDARIO ----------------
    "blackout": ("Blackout de recompras (estimado)", """
<p><b>Qué es.</b> Periodo en el que la mayoría de empresas deja de recomprar acciones propias por iniciativa propia: desde unas 5 semanas antes de publicar resultados hasta unas 48 horas después. Según Goldman Sachs, las recompras son la mayor fuente de demanda de acciones en EE. UU.</p>
<p><b>Cuidado.</b> No hay dato oficial ni obligación legal: es una práctica de las empresas. Aquí se estima como una franja por trimestre.</p>""",
        [("Goldman Sachs (Kostin), vía Business Insider", "https://www.businessinsider.in/goldman-were-blaming-the-stock-market-sell-off-on-a-pullback-in-buybacks/articleshow/44889114.cms"),
         ("Goldman Sachs: las 48 horas posteriores, vía MarketWatch", "https://macdailynews.com/2015/03/24/goldman-sachs-buy-apple-during-buyback-blackout-period/"),
         ("Goldman Sachs: peso de las recompras, vía Bloomberg", "https://www.bloombergquint.com/markets/goldman-considers-a-world-without-buybacks-it-looks-ominous")]),
    "ciclo": ("Ciclo presidencial", """
<p>Rentabilidad media del S&amp;P 500 según el año del mandato presidencial de EE. UU. (datos de Shiller desde 1871).</p>
<p><b>Cuidado.</b> Es un patrón popular sin respaldo académico sólido para el año del mandato; la literatura académica más citada (Santa-Clara y Valkanov, 2003) estudia el partido del presidente, no el año del ciclo. Pocas observaciones: unas 38 por cada año del ciclo.</p>""",
        [("Santa-Clara y Valkanov (2003), Journal of Finance", "https://doi.org/10.1111/1540-6261.00590")]),
    "estacionalidad": ("Estacionalidad mensual", """
<p>Rentabilidad media y mediana del S&amp;P 500 en cada mes del año, y porcentaje de años en que ese mes fue positivo.</p>
<p><b>Evidencia.</b> El efecto más estudiado es "Sell in May" (efecto Halloween): Bouman y Jacobsen (2002) hallaron mejores rentabilidades de noviembre a abril en 36 de 37 mercados. En EE. UU., otros autores atribuyen el resultado a dos meses extremos (octubre de 1987 y agosto de 1998).</p>
<p><b>Cuidado.</b> Efectos débiles e inestables; no deben pesar más que el régimen.</p>""",
        [("Bouman y Jacobsen (2002), American Economic Review", "https://doi.org/10.1257/000282802762024683"),
         ("Crítica en Econ Journal Watch", "https://econjwatch.org/articles/stock-market-efficiency-withstands-another-challenge-solving-the-sell-in-may-buy-after-halloween-puzzle")]),
    "cambio_mes": ("Efecto cambio de mes", """
<p>Las bolsas tienden a subir más en el último día hábil del mes y los tres primeros del siguiente (Lakonishok y Smidt, 1988; Ariel, 1987). Una de las explicaciones propuestas son los flujos de dinero periódicos de fin de mes (nóminas, pensiones, fondos).</p>
<p><b>Cuidado.</b> Útil, como mucho, para afinar el día de una compra ya decidida.</p>""",
        [("McConnell y Xu: el efecto persiste", "https://docs.lib.purdue.edu/ciberwp/43"),
         ("Ariel (1987), Journal of Financial Economics", "https://doi.org/10.1016/0304-405X(87)90066-3")]),
    "Opciones": ("Vencimientos de opciones", """
<ul><li><b>Mensual:</b> tercer viernes de cada mes. Al vencer, se liberan coberturas y cambia la gamma.</li>
<li><b>Trimestral:</b> tercer viernes de marzo, junio, septiembre y diciembre. Vencen a la vez opciones y futuros sobre índices y acciones, y además se rebalancea el S&amp;P 500. Mucho volumen al cierre.</li>
<li><b>VIX:</b> miércoles, 30 días antes del vencimiento mensual del mes siguiente.</li></ul>""", []),
    "Cierres": ("Cierres de trimestre y de año", """
<p>Las gestoras y los fondos de pensiones rebalancean sus carteras y "maquillan" posiciones antes de publicar resultados. Puede haber flujos grandes los últimos días, sobre todo tras trimestres con mucha diferencia entre bolsa y bonos.</p>""", []),
    "Recompras": ("Recompras", """<p>Inicio y fin estimados del blackout de recompras de cada trimestre. Ver la tarjeta "Blackout de recompras".</p>""", []),
    "Índices": ("Reconstitución de índices Russell", """
<p>FTSE Russell recompone sus índices de EE. UU. (Russell 1000, 2000…). Desde 2026 lo hace dos veces al año, en junio y diciembre. Genera un volumen enorme al cierre del día efectivo, sobre todo en small caps. El día de referencia fija qué empresas entran y salen.</p>""",
        [("FTSE Russell: reconstitución de diciembre de 2026", "https://www.lseg.com/en/media-centre/press-releases/ftse-russell/2026/ftse-russell-begins-december-2026-semi-annual-russell-us-indexes-reconstitution")]),
    "Fed": ("Reuniones de la Fed (FOMC)", """
<p>Decisión de tipos de interés y, en las reuniones trimestrales, nuevas previsiones. El día de la decisión aumenta la volatilidad de bonos, dólar y bolsa.</p>""",
        [("Reserva Federal: calendario", "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm")]),
    "Datos": ("IPC de EE. UU.", """
<p>Publicación mensual de la inflación (8:30 de Nueva York; normalmente 14:30 en España). Es uno de los datos que más mueve los mercados mientras la inflación sea el centro de la política de la Fed.</p>""",
        [("BLS: calendario del IPC", "https://www.bls.gov/schedule/news_release/cpi.htm")]),
    "Fiscal": ("Fechas fiscales", """
<ul><li><b>31 de marzo:</b> cierre del año fiscal en Japón (repatriación y rebalanceo de inversores japoneses).</li>
<li><b>15 de abril:</b> pago de impuestos en EE. UU. (salida de liquidez).</li>
<li><b>31 de octubre:</b> fin de ejercicio fiscal de muchos fondos de inversión estadounidenses (ventas para materializar pérdidas).</li>
<li><b>Diciembre:</b> ventas de particulares para compensar pérdidas en impuestos; Roll (1983) lo relacionó con el rebote de enero.</li></ul>""", []),
}


# ----------------------------------------------------------------------------
# CONFIGURACIÓN
# ----------------------------------------------------------------------------

CARPETA_WEB = "docs"
CARPETA_DATOS = "datos"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}

AHORA = pd.Timestamp.now(tz="UTC").tz_convert(None)
HOY = AHORA.normalize()
# Datos completos hasta ayer; última semana cerrada = último viernes <= ayer
_REF = HOY - pd.Timedelta(days=1)
ULTIMO_VIERNES = _REF - pd.Timedelta(days=(_REF.weekday() - 4) % 7)

MOTORES = [
    ("ACWI", "RV global"), ("SPY", "RV EE. UU."), ("RSP", "RV EE. UU. equiponderada"),
    ("VGK", "RV Europa"), ("EEM", "RV emergentes"), ("QQQ", "Nasdaq"),
    ("IWM", "Small caps EE. UU."), ("VTV", "Value"), ("VUG", "Growth"),
    ("MTUM", "Momentum"), ("QUAL", "Calidad"), ("BTAL", "Anti-beta"),
    ("TLT", "Bonos largos"), ("IEF", "Bonos medios"), ("TIP", "Bonos ligados a inflación"),
    ("HYG", "High yield"), ("DBC", "Materias primas"), ("XLE", "Energía"),
    ("GLD", "Oro"), ("URA", "Uranio"), ("UUP", "Dólar"), ("FXE", "Euro"),
    ("DBMF", "Managed futures"), ("VIXY", "Volatilidad"),
]
REFERENCIA = "BIL"
OTROS_TIINGO = ["CPER", "URTH", "IEMG"]

# Índices para las condiciones de indexación: (ticker, nombre, ¿EE. UU.?)
INDICES_COND = [("URTH", "MSCI World", False), ("SPY", "S&P 500", True),
                ("RSP", "S&P 500 equiponderado", True), ("VGK", "Europa", False),
                ("IEMG", "Emergentes (MSCI EM IMI)", False)]

GRUPO = {
    "ACWI": "Renta variable", "SPY": "Renta variable", "RSP": "Renta variable", "VGK": "Renta variable",
    "EEM": "Renta variable", "QQQ": "Renta variable", "IWM": "Renta variable",
    "VTV": "Factores", "VUG": "Factores", "MTUM": "Factores", "QUAL": "Factores",
    "TLT": "Renta fija", "IEF": "Renta fija", "TIP": "Renta fija", "HYG": "Crédito",
    "DBC": "Materias primas", "XLE": "Materias primas", "GLD": "Materias primas", "URA": "Materias primas",
    "UUP": "Divisas", "FXE": "Divisas", "BTAL": "Alternativos", "DBMF": "Alternativos", "VIXY": "Alternativos",
}
COLOR_GRUPO = {"Renta variable": "#2f6fdb", "Factores": "#8e44ad", "Renta fija": "#1f9d55",
               "Crédito": "#a0672a", "Materias primas": "#e67e22", "Divisas": "#c0392b",
               "Alternativos": "#16a085"}
# Familias que no entran en el TOP 3 (coberturas y estrategias, no motores para rotar por tendencia)
EXCLUIR_TOP = {"Alternativos"}
MOTOR_INICIAL = "SPY"       # único motor marcado al abrir la página
B100_SEMANAS = 260          # historia máxima de la vista en base 100 (5 años)
DESC = {
    "ACWI": "iShares MSCI ACWI: bolsa mundial, países desarrollados y emergentes.",
    "SPY": "SPDR S&P 500: las 500 mayores empresas de EE. UU., ponderadas por tamaño.",
    "RSP": "Invesco S&P 500 Equal Weight: las mismas 500 empresas, todas con el mismo peso.",
    "VGK": "Vanguard FTSE Europe: bolsa de Europa desarrollada.",
    "EEM": "iShares MSCI Emerging Markets: bolsa de países emergentes.",
    "QQQ": "Invesco QQQ: Nasdaq-100, las 100 mayores empresas no financieras del Nasdaq (sesgo tecnológico).",
    "IWM": "iShares Russell 2000: empresas pequeñas de EE. UU.",
    "VTV": "Vanguard Value: empresas de EE. UU. baratas por valoración.",
    "VUG": "Vanguard Growth: empresas de EE. UU. de alto crecimiento.",
    "MTUM": "iShares MSCI USA Momentum: acciones de EE. UU. con mejor comportamiento reciente.",
    "QUAL": "iShares MSCI USA Quality: empresas de EE. UU. rentables, poco endeudadas y estables.",
    "BTAL": "Fondo anti-beta de AGF: compra acciones de beta baja y vende las de beta alta en igual importe. Aproximación a market neutral.",
    "TLT": "iShares 20+ Year Treasury: bonos del Tesoro de EE. UU. a más de 20 años.",
    "IEF": "iShares 7-10 Year Treasury: bonos del Tesoro de EE. UU. a 7-10 años.",
    "TIP": "iShares TIPS: bonos del Tesoro de EE. UU. ligados a la inflación.",
    "HYG": "iShares iBoxx High Yield: bonos corporativos de alto rendimiento en dólares.",
    "DBC": "Invesco DB Commodity: cesta diversificada de futuros de materias primas.",
    "XLE": "Energy Select Sector SPDR: sector energía del S&P 500.",
    "GLD": "SPDR Gold Shares: oro físico.",
    "URA": "Global X Uranium: empresas de minería y combustible de uranio.",
    "UUP": "Invesco DB US Dollar Bullish: dólar frente a una cesta de divisas desarrolladas.",
    "FXE": "Invesco CurrencyShares Euro: euro frente al dólar.",
    "DBMF": "iMGP DBi Managed Futures: réplica de los fondos de gestión tendencial.",
    "VIXY": "ProShares VIX Short-Term Futures: futuros del VIX a corto plazo. Sube en los picos de miedo y pierde valor con el tiempo en mercados tranquilos.",
    "BIL": "SPDR 1-3 Month T-Bill: letras del Tesoro de EE. UU., la referencia de liquidez.",
    "URTH": "iShares MSCI World: bolsa de países desarrollados.",
    "IEMG": "iShares Core MSCI Emerging Markets: emergentes de empresas grandes, medianas y pequeñas (índice IMI).",
    "CPER": "United States Copper Index Fund: futuros de cobre.",
}


def tk(t):
    return f'<span class="tk" data-tip="{html.escape(t + " · " + DESC.get(t, ""))}">{html.escape(t)}</span>'


FRED_SERIES = {
    "T10YIE": "Inflación implícita 10 años",
    "CPIAUCSL": "IPC EE. UU.",
    "DFII10": "Tipo real 10 años (TIPS)",
    "ICSA": "Peticiones de subsidio",
    "SAHMREALTIME": "Regla de Sahm",
    "GACDFSA066MSFRBPHI": "Encuesta Fed Filadelfia",
    "GACDISA066MSFRBNY": "Encuesta Fed Nueva York",
    "BAMLH0A0HYM2": "Diferencial high yield (3 años)",
    "BAA10Y": "Diferencial Baa − bono 10 años",
    "T10Y3M": "Curva 10 años − 3 meses",
    "NFCI": "Condiciones financieras (Chicago Fed)",
    "STLFSI4": "Estrés financiero (St. Louis Fed)",
    "WALCL": "Balance de la Fed",
    "WTREGEN": "Cuenta del Tesoro",
    "RRPONTSYD": "Repo inverso",
    "DTWEXBGS": "Dólar (índice amplio)",
}

CBOE_INDICES = ["VIX", "VIX3M", "COR3M", "DSPX", "SKEW"]

# Calendarios oficiales (revisar cada año)
FOMC = [
    ("2026-12-08", "2026-12-09"),
    ("2027-01-26", "2027-01-27"), ("2027-03-16", "2027-03-17"),
    ("2027-04-27", "2027-04-28"), ("2027-06-08", "2027-06-09"),
    ("2027-07-27", "2027-07-28"), ("2027-09-14", "2027-09-15"),
    ("2027-10-26", "2027-10-27"), ("2027-11-16", "2027-11-17"),
]
IPC_EEUU = [
    "2026-12-10", "2027-01-13", "2027-02-11", "2027-03-10", "2027-04-13",
    "2027-05-12", "2027-06-10", "2027-07-14", "2027-08-11", "2027-09-14",
    "2027-10-14", "2027-11-10", "2027-12-10",
]

ERRORES = []   # (fuente, mensaje)
FECHAS = {}    # fuente -> última fecha disponible


# ----------------------------------------------------------------------------
# UTILIDADES
# ----------------------------------------------------------------------------

INICIO = time.time()
LIMITE_DESCARGAS = INICIO + 10 * 60   # nunca más de 10 minutos descargando
SERVIDOR_FALLOS = {}                   # servidor -> fallos seguidos
SERVIDOR_CAIDO = set()                 # servidores que no se vuelven a intentar hoy


class NoEncontrado(Exception):
    pass


def get(url, params=None, intentos=2, espera=2, timeout=(10, 30)):
    from urllib.parse import urlparse
    host = urlparse(url).netloc
    if host in SERVIDOR_CAIDO:
        raise RuntimeError(f"{host} no responde hoy (omitido)")
    ultimo = ""
    for i in range(intentos):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=timeout)
            if r.status_code == 200:
                SERVIDOR_FALLOS[host] = 0
                return r
            if r.status_code == 404:
                raise NoEncontrado("HTTP 404")
            if r.status_code == 429:          # límite de peticiones: no insistir
                SERVIDOR_CAIDO.add(host)
                raise RuntimeError("HTTP 429 (límite de peticiones)")
            ultimo = f"HTTP {r.status_code}"
            if 400 <= r.status_code < 500:
                raise RuntimeError(ultimo)
        except (NoEncontrado, RuntimeError):
            raise
        except Exception as e:  # noqa  (tiempo agotado, conexión cortada...)
            ultimo = str(e)[:120]
        if i < intentos - 1:
            time.sleep(espera)
    SERVIDOR_FALLOS[host] = SERVIDOR_FALLOS.get(host, 0) + 1
    if SERVIDOR_FALLOS[host] >= 2:
        SERVIDOR_CAIDO.add(host)
    raise RuntimeError(ultimo)


def seguro(nombre, fn, *a, **k):
    if time.time() > LIMITE_DESCARGAS:
        ERRORES.append((nombre, "sin tiempo"))
        print(f"[{time.time() - INICIO:5.0f}s] {nombre}: SALTADO (sin tiempo)", flush=True)
        return None
    t0 = time.time()
    try:
        r = fn(*a, **k)
        print(f"[{time.time() - INICIO:5.0f}s] {nombre}: ok ({time.time() - t0:.0f}s)", flush=True)
        return r
    except Exception as e:  # noqa
        ERRORES.append((nombre, str(e)[:180]))
        print(f"[{time.time() - INICIO:5.0f}s] {nombre}: FALLO ({time.time() - t0:.0f}s) {str(e)[:120]}", flush=True)
        return None


def semanal(s):
    if s is None or len(s) == 0:
        return None
    w = s.resample("W-FRI").last().dropna()
    return w[w.index <= ULTIMO_VIERNES]


def mensual(s):
    if s is None or len(s) == 0:
        return None
    return s.resample("MS").last().dropna()


def pct(s, valor=None):
    s = s.dropna()
    if len(s) == 0:
        return np.nan
    v = s.iloc[-1] if valor is None else valor
    return float((s <= v).mean() * 100)


def rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def fmt(x, dec=2, suf=""):
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return "—"
    return f"{x:,.{dec}f}{suf}".replace(",", "X").replace(".", ",").replace("X", ".")


def e(t):
    return html.escape(str(t))


def fd(t):
    """Fecha con estilo propio (fuente y color distintos del dato)."""
    return f'<span class="fd">{e(t)}</span>'


def con_fechas(t):
    """Escapa un texto y envuelve en estilo de fecha las fechas dd-mm-aaaa, dd-mm y mm-aaaa que contenga."""
    return re.sub(r"(?<![\d,.])(\d{2}-\d{2}-\d{4}|\d{2}-\d{4}|\d{2}-\d{2})(?![\d,.])",
                  lambda mo: f'<span class="fd">{mo.group(1)}</span>', e(t))


def i(k):
    return f' <button class="info" data-k="{html.escape(k)}" title="¿Qué es?">ⓘ</button>' if k in INFO else ""


def info_json():
    datos = {}
    for k, (tit, cuerpo, fuentes) in INFO.items():
        f = ""
        if fuentes:
            f = '<p class="fuentes-info"><b>Fuentes:</b> ' + " · ".join(
                f'<a href="{html.escape(u)}" target="_blank" rel="noopener">{html.escape(n)}</a>' for n, u in fuentes) + "</p>"
        datos[k] = {"t": tit, "h": cuerpo.strip() + f}
    return json.dumps(datos, ensure_ascii=False).replace("</", "<\\/")


def antiguedad_ok(fecha, frecuencia):
    if fecha is None:
        return False
    limite = {"d": 7, "s": 16, "m": 75}[frecuencia]
    return (HOY - pd.Timestamp(fecha)).days <= limite


def registrar(nombre, s):
    if s is not None and len(s):
        FECHAS[nombre] = s.index[-1]


# ----------------------------------------------------------------------------
# DESCARGAS
# ----------------------------------------------------------------------------

def fred(sid):
    clave = os.environ.get("FRED_API_KEY", "").strip()
    if not clave:
        raise RuntimeError("falta la clave de FRED")
    r = get("https://api.stlouisfed.org/fred/series/observations",
            params={"series_id": sid, "api_key": clave, "file_type": "json"})
    obs = r.json().get("observations", [])
    s = pd.Series(pd.to_numeric([o.get("value") for o in obs], errors="coerce"),
                  index=pd.to_datetime([o.get("date") for o in obs], errors="coerce"))
    s = s[s.index.notna()].dropna().sort_index()
    if len(s) == 0:
        raise RuntimeError("serie vacía")
    return s.rename(sid)


def cboe_indice(nombre):
    url = f"https://cdn-api.cboe.com/api/global/us_indices/daily_prices/{nombre}_History.csv"
    df = pd.read_csv(io.StringIO(get(url).text))
    s = pd.Series(pd.to_numeric(df.iloc[:, -1], errors="coerce").values,
                  index=pd.to_datetime(df.iloc[:, 0], format="%m/%d/%Y", errors="coerce"))
    s = s[s.index.notna()].dropna().sort_index()
    if len(s) == 0:
        raise RuntimeError("serie vacía")
    return s.rename(nombre)


def tiingo(ticker, frecuencia="weekly"):
    clave = os.environ.get("TIINGO_API_KEY", "").strip()
    if not clave:
        raise RuntimeError("falta la clave de Tiingo")
    r = get(f"https://api.tiingo.com/tiingo/daily/{ticker}/prices",
            params={"startDate": "1990-01-01", "resampleFreq": frecuencia, "token": clave}, intentos=1)
    d = r.json()
    if not isinstance(d, list) or not d:
        raise RuntimeError(str(d)[:120])
    df = pd.DataFrame(d)
    idx = pd.to_datetime(df["date"], utc=True).dt.tz_convert(None).dt.normalize()
    s = pd.Series(df["adjClose"].astype(float).values, index=idx).sort_index()
    return s.rename(ticker)


def _leer_excel(contenido):
    try:
        import python_calamine  # noqa
    except ImportError:
        import subprocess
        import sys
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "python-calamine"], check=False)
    try:
        return pd.read_excel(io.BytesIO(contenido), sheet_name="Data", header=None, engine="calamine")
    except Exception:  # noqa
        return pd.read_excel(io.BytesIO(contenido), sheet_name="Data", header=None)


def shiller():
    pagina = get("https://shillerdata.com/").text
    enlaces = re.findall(r'href="([^"]*ie_data\.xls[^"]*)"', pagina)
    if not enlaces:
        raise RuntimeError("no se encuentra el archivo")
    url = html.unescape(enlaces[0])
    if url.startswith("//"):
        url = "https:" + url
    raw = _leer_excel(get(url, timeout=(10, 60)).content)

    col0 = pd.to_numeric(raw.iloc[:, 0], errors="coerce")
    arranque = col0[(col0 > 1870) & (col0 < 1872)]
    if arranque.empty:
        raise RuntimeError("formato no reconocido (fechas)")
    inicio = raw.index.get_loc(arranque.index[0])
    cab = raw.iloc[:inicio]
    textos = [" ".join(str(v) for v in cab.iloc[:, k] if pd.notna(v)).upper() for k in range(raw.shape[1])]
    datos = raw.iloc[inicio:]
    fecha = pd.to_numeric(datos.iloc[:, 0], errors="coerce")
    datos = datos[fecha.notna()]
    fecha = fecha[fecha.notna()]

    def col(k):
        return pd.to_numeric(datos.iloc[:, k], errors="coerce").reset_index(drop=True)

    def ultimo(k):
        v = col(k).dropna()
        return v.iloc[-1] if len(v) else np.nan

    def buscar(cond, rango):
        for k, t in enumerate(textos):
            if cond(t) and rango[0] <= ultimo(k) <= rango[1]:
                return k
        return None

    k_cape = buscar(lambda t: "CAPE" in t and "TR" not in t and "YIELD" not in t and "EXCESS" not in t, (5, 80))
    if k_cape is None:
        raise RuntimeError("formato no reconocido (CAPE)")
    k_ecy = buscar(lambda t: "YIELD" in t and ("CAPE" in t or "EXCESS" in t), (-0.2, 0.3))
    k_gs10 = buscar(lambda t: "GS10" in t, (0, 20))
    k_cpi = buscar(lambda t: "CPI" in t, (50, 1000))
    k_gs10 = 6 if k_gs10 is None else k_gs10
    k_cpi = 4 if k_cpi is None else k_cpi

    anio = np.floor(fecha.values).astype(int)
    mes = np.clip(np.round((fecha.values - anio) * 100).astype(int), 1, 12)
    idx = pd.to_datetime(pd.DataFrame({"year": anio, "month": mes, "day": 1}))
    out = pd.DataFrame({"P": col(1).values, "CPI": col(k_cpi).values,
                        "GS10": col(k_gs10).values, "CAPE": col(k_cape).values}, index=idx.values)
    infl10 = (out["CPI"] / out["CPI"].shift(120)) ** 0.1 - 1
    out["ECY"] = 100 / out["CAPE"] - (out["GS10"] - 100 * infl10)
    if k_ecy is not None:                      # cifra publicada por Shiller, si existe
        out["ECY"] = col(k_ecy).values * 100
    out = out[~out.index.duplicated(keep="last")].sort_index()
    ult = out["ECY"].dropna()
    if ult.empty or not (-5 <= ult.iloc[-1] <= 15):
        raise RuntimeError("valor de ECY fuera de rango")
    return out


def fcig():
    url = "https://www.federalreserve.gov/econres/notes/feds-notes/fci_g_public_monthly_3yr.csv"
    df = pd.read_csv(io.StringIO(get(url).text))
    cols = [c for c in df.columns[1:] if "fci" in str(c).lower()]
    col = cols[0] if cols else df.columns[1]
    s = pd.Series(pd.to_numeric(df[col], errors="coerce").values,
                  index=pd.to_datetime(df.iloc[:, 0], errors="coerce"))
    s = s[s.index.notna()].dropna().sort_index()
    if len(s) == 0:
        raise RuntimeError("serie vacía")
    return s.rename("FCIG")


def cot(dataset, codigo, largo, corto):
    r = get(f"https://publicreporting.cftc.gov/resource/{dataset}.json",
            params={"cftc_contract_market_code": codigo,
                    "$order": "report_date_as_yyyy_mm_dd DESC", "$limit": 1500})
    df = pd.DataFrame(r.json())
    if df.empty:
        raise RuntimeError("sin datos")
    idx = pd.to_datetime(df["report_date_as_yyyy_mm_dd"])
    neto = (pd.to_numeric(df[largo]) - pd.to_numeric(df[corto])) / pd.to_numeric(df["open_interest_all"]) * 100
    return pd.Series(neto.values, index=idx).sort_index()


# ----------------------------------------------------------------------------
# HISTÓRICOS PROPIOS (put/call y gamma)
# ----------------------------------------------------------------------------

def leer_hist(nombre):
    ruta = os.path.join(CARPETA_DATOS, nombre)
    if os.path.exists(ruta):
        df = pd.read_csv(ruta, parse_dates=["fecha"]).set_index("fecha").sort_index()
        return df[~df.index.duplicated(keep="last")]
    return pd.DataFrame()


def guardar_hist(nombre, df):
    os.makedirs(CARPETA_DATOS, exist_ok=True)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df.index.name = "fecha"
    df.to_csv(os.path.join(CARPETA_DATOS, nombre))


PC_CLAVES = {
    "total": "TOTAL PUT/CALL RATIO",
    "acciones": "EQUITY PUT/CALL RATIO",
    "indices": "INDEX PUT/CALL RATIO",
    "spx": "SPX",
}


def putcall_dia(fecha):
    url = f"https://cdn.cboe.com/data/us/options/market_statistics/daily/{fecha:%Y-%m-%d}_daily_options"
    try:
        r = get(url, intentos=1, timeout=(10, 15))
    except NoEncontrado:
        return None
    d = r.json()
    ratios = d.get("ratios", []) if isinstance(d, dict) else []
    fila = {}
    for it in ratios:
        nombre = str(it.get("name", "")).upper()
        try:
            valor = float(it.get("value"))
        except Exception:  # noqa
            continue
        if nombre == "TOTAL PUT/CALL RATIO":
            fila["total"] = valor
        elif nombre == "EQUITY PUT/CALL RATIO":
            fila["acciones"] = valor
        elif nombre == "INDEX PUT/CALL RATIO":
            fila["indices"] = valor
        elif nombre.startswith("SPX + SPXW") and "PUT/CALL" in nombre:
            fila["spx"] = valor
    return fila or None


def actualizar_putcall():
    hist = leer_hist("putcall.csv")
    dias = pd.bdate_range(HOY - pd.Timedelta(days=400), HOY - pd.Timedelta(days=1))
    pendientes = [d for d in dias if d not in hist.index]
    pendientes = pendientes[-300:]
    nuevas = {}
    fallos = 0
    limite = min(time.time() + 180, LIMITE_DESCARGAS)   # como mucho 3 minutos por ejecución
    for d in reversed(pendientes):
        if time.time() > limite:
            break
        try:
            fila = putcall_dia(d)
        except Exception:  # noqa  servidor caído: dejarlo para mañana
            break
        if fila:
            nuevas[d] = fila
            fallos = 0
        else:
            fallos += 1
        if fallos >= 15:   # festivos o fin del archivo disponible
            break
        time.sleep(0.3)
    if nuevas:
        hist = pd.concat([hist, pd.DataFrame.from_dict(nuevas, orient="index")])
        guardar_hist("putcall.csv", hist)
    if hist.empty:
        raise RuntimeError("sin datos")
    return hist.sort_index()


def _gamma_bs(S, K, T, iv):
    sq = np.sqrt(T)
    d1 = (np.log(S / K) + 0.5 * iv ** 2 * T) / (iv * sq)
    return np.exp(-0.5 * d1 ** 2) / np.sqrt(2 * np.pi) / (S * iv * sq)


def gamma_spx(spy_ultimo=None):
    d = get("https://cdn-api.cboe.com/api/global/delayed_quotes/options/_SPX.json", timeout=120).json()
    datos = d["data"]
    op = pd.DataFrame(datos["options"])
    m = op["option"].str.extract(r"^([A-Z]+)(\d{6})([CP])(\d{8})$")
    op["vto"] = pd.to_datetime(m[1], format="%y%m%d", errors="coerce") + pd.Timedelta(hours=21)
    op["tipo"] = m[2]
    op["K"] = pd.to_numeric(m[3], errors="coerce") / 1000
    for c in ["iv", "open_interest", "bid", "ask"]:
        op[c] = pd.to_numeric(op[c], errors="coerce")
    op = op.dropna(subset=["vto", "tipo", "K"])
    op["T"] = (op["vto"] - AHORA).dt.total_seconds() / (365 * 86400)
    op = op[op["T"] > 0]

    spot = None
    for clave in ("current_price", "close", "last_trade_price", "price"):
        v = datos.get(clave) if isinstance(datos, dict) else None
        try:
            if v and float(v) > 0:
                spot = float(v)
                break
        except Exception:  # noqa
            pass
    if spot is None:   # paridad put-call en el vencimiento más cercano
        cerc = op[op["vto"] == op["vto"].min()].copy()
        cerc["mid"] = (cerc["bid"] + cerc["ask"]) / 2
        tabla = cerc.pivot_table(index="K", columns="tipo", values="mid").dropna()
        tabla = tabla[(tabla["C"] > 0) & (tabla["P"] > 0)]
        k = (tabla["C"] - tabla["P"]).abs().idxmin()
        spot = float(k + tabla.loc[k, "C"] - tabla.loc[k, "P"])

    if spy_ultimo:
        if abs(spot / (spy_ultimo * 10) - 1) > 0.04:
            raise RuntimeError("precio del S&P 500 incoherente")
    op = op[(op["open_interest"] > 0) & (op["iv"] > 0.01) & (op["T"] > 1 / 365) & (op["T"] < 1.5)]
    signo = np.where(op["tipo"].values == "C", 1.0, -1.0)
    K = op["K"].values
    T = op["T"].values
    iv = op["iv"].values
    oi = op["open_interest"].values

    def gex(S):
        g = _gamma_bs(S, K, T, iv)
        return float(np.nansum(signo * g * oi * 100 * S * S * 0.01))

    actual = gex(spot)
    rejilla = np.linspace(spot * 0.85, spot * 1.15, 121)
    perfil = np.array([gex(s) for s in rejilla])
    giro = None
    cambios = np.where(np.diff(np.sign(perfil)) != 0)[0]
    if len(cambios):
        mejores = sorted(cambios, key=lambda i: abs(rejilla[i] - spot))
        i = mejores[0]
        x0, x1, y0, y1 = rejilla[i], rejilla[i + 1], perfil[i], perfil[i + 1]
        giro = float(x0 - y0 * (x1 - x0) / (y1 - y0))

    cerca = op[op["T"] < 70 / 365]
    calls = cerca[cerca["tipo"] == "C"].groupby("K")["open_interest"].sum()
    puts = cerca[cerca["tipo"] == "P"].groupby("K")["open_interest"].sum()
    muro_call = float(calls[calls.index >= spot].idxmax()) if len(calls[calls.index >= spot]) else None
    muro_put = float(puts[puts.index <= spot].idxmax()) if len(puts[puts.index <= spot]) else None

    res = {"spot": spot, "gex_mm": actual / 1e9, "giro": giro,
           "muro_call": muro_call, "muro_put": muro_put,
           "fecha": str(d.get("timestamp", AHORA))}
    hist = leer_hist("gamma.csv")
    fila = pd.DataFrame({"gex_mm": [res["gex_mm"]],
                         "dist_giro_pct": [((spot / giro - 1) * 100) if giro else np.nan]},
                        index=[HOY])
    hist = pd.concat([hist, fila])
    guardar_hist("gamma.csv", hist)
    res["hist"] = hist.sort_index()
    return res


# ----------------------------------------------------------------------------
# CÁLCULOS DEL DIAGNOSTICADOR
# ----------------------------------------------------------------------------

def rrg(precio_w, ref_w):
    df = pd.concat([precio_w, ref_w], axis=1, join="inner").dropna()
    rs = df.iloc[:, 0] / df.iloc[:, 1]
    X = 100 * rs / rs.rolling(26).mean()
    Y = 100 * X / X.shift(4)
    return rs, X.dropna(), Y.dropna()


def fase_motor(precio_w, rs, X, Y):
    x, y = X.iloc[-1], Y.iloc[-1]
    dy = Y.diff().iloc[-3:]
    sube3 = bool((dy > 0).all())
    baja3 = bool((dy < 0).all())
    rr = rsi(precio_w)
    r = rr.iloc[-1]
    dist = (precio_w / precio_w.rolling(40).mean() - 1).dropna()
    pd_ = pct(dist)
    prs = pct(rs)
    # 4.ª señal: divergencia. Precio en la zona de máximos (mínimos) de 26 semanas con el RSI ya
    # 10 puntos por debajo (encima) de su máximo (mínimo) de esas 26 semanas.
    c26, r26 = precio_w.iloc[-26:], rr.iloc[-26:]
    div_alc = bool(precio_w.iloc[-1] >= 0.98 * c26.max() and r <= r26.max() - 10)
    div_baj = bool(precio_w.iloc[-1] <= 1.02 * c26.min() and r >= r26.min() + 10)
    desde = precio_w.index[0].year
    s_alc = [n for n, ok in (
        (f"RSI semanal por encima de 70 ({fmt(r, 0)})", r > 70),
        (f"precio muy alejado por encima de su media de 40 semanas (10 % más extremo desde {desde})", pd_ >= 90),
        (f"fuerza frente a la liquidez en máximos (percentil {fmt(prs, 0)} desde {desde})", prs > 90),
        ("divergencia: precio en máximos de 6 meses, pero con el RSI ya más bajo que en el pico", div_alc)) if ok]
    s_baj = [n for n, ok in (
        (f"RSI semanal por debajo de 30 ({fmt(r, 0)})", r < 30),
        (f"precio muy alejado por debajo de su media de 40 semanas (10 % más extremo desde {desde})", pd_ <= 10),
        (f"fuerza frente a la liquidez en mínimos (percentil {fmt(prs, 0)} desde {desde})", prs < 10),
        ("divergencia: precio en mínimos de 6 meses, pero con el RSI ya más alto que en el fondo", div_baj)) if ok]
    alc, baj = len(s_alc), len(s_baj)
    if x >= 100 and y < 100:
        f = "GIRO"
    elif x >= 100:
        f = "MADUREZ" if (baja3 or alc >= 2) else "ACELERACIÓN"
    elif y >= 100:
        f = "ARRANQUE"
    else:
        f = "SUELO" if (sube3 or baj >= 2) else "REZAGADO"
    return f, {"rsi": r, "alc": alc, "baj": baj, "s_alc": s_alc, "s_baj": s_baj}


CLAVE_COND = {"Precio cerca de la media de 200 semanas": "c_ma200",
              "Crédito girando a la baja desde su pico": "c_credito",
              "VIX vuelve a contango tras inversión": "c_vix",
              "RSI semanal en sobreventa": "c_rsi"}
CLAVE_BLOQUE = {"A · Régimen": "b_A", "B · Estrés": "b_B", "C · Liquidez": "b_C",
                "E+F+G · Valoración, técnico y sentimiento": "b_EFG"}

ACCION = {
    "SUELO": "Vigilar", "ARRANQUE": "Construir poco a poco", "ACELERACIÓN": "Mantener",
    "MADUREZ": "Empezar a salir", "GIRO": "Salir", "REZAGADO": "No tocar",
}
COLOR_FASE = {
    "SUELO": "#6b7a90", "ARRANQUE": "#2f6fdb", "ACELERACIÓN": "#1f9d55",
    "MADUREZ": "#d9a400", "GIRO": "#e0661b", "REZAGADO": "#c0392b",
}


def fase_macro(nivel_pct, impulso, frenando3):
    if nivel_pct >= 50 and impulso > 0:
        return "MADUREZ" if (nivel_pct > 80 and frenando3) else "ACELERACIÓN"
    if nivel_pct >= 50:
        return "GIRO"
    if impulso > 0:
        return "ARRANQUE"
    return "SUELO" if (nivel_pct < 20 and frenando3) else "REZAGADO"


def variable_macro(nombre, s, frecuencia, periodos, modo="dif"):
    """s: serie ya en su frecuencia (semanal o mensual)."""
    s = s.dropna()
    if len(s) < periodos + 10:
        return None
    imp = (s / s.shift(periodos) - 1) * 100 if modo == "pct" else s - s.shift(periodos)
    imp = imp.dropna()
    frenando3 = bool((imp.abs().diff().iloc[-3:] < 0).all())
    acelera3 = bool((imp.abs().diff().iloc[-3:] > 0).all())
    niv = pct(s)
    ultimo = imp.iloc[-1]
    fase = fase_macro(niv, ultimo, frenando3)
    desde = s.index[-1] - pd.Timedelta(weeks=24)
    ventana = imp[imp.index >= desde].index
    rastro_x = [pct(s, s.loc[d]) for d in ventana]
    rastro_y = [pct(imp, imp.loc[d]) for d in ventana]
    return {"nombre": nombre, "valor": s.iloc[-1], "fecha": s.index[-1], "nivel": niv,
            "impulso": ultimo, "fase": fase, "frecuencia": frecuencia,
            "rx": rastro_x, "ry": rastro_y, "modo": modo, "periodos": periodos,
            "fechas": list(ventana), "vals": [float(s.loc[d]) for d in ventana],
            "frenando3": frenando3, "acelera3": acelera3, "nivel_imp": pct(imp),
            "desde": s.index[0].year, "desde_imp": imp.index[0].year}


V_MEMORIA = 13


def puerta_comun(baa_w, ratio_vix_w):
    """Condiciones comunes a todos los índices: crédito, VIX y veto."""
    res = {"credito": None, "vix": None, "veto": False, "baa_13s": None, "ratio_vix": None}
    V = V_MEMORIA
    if baa_w is not None and len(baa_w) > 120:
        b = baa_w.values
        sma4 = baa_w.rolling(4).mean().values
        sma13 = baa_w.rolling(13).mean().values
        n = len(b)
        hits = []
        for k in range(n - V, n):
            ini = max(0, k - 25)
            j = ini + int(np.argmax(b[ini:k + 1]))
            M = b[j]
            previo = b[max(0, j - 52):j]
            if len(previo) == 0:
                hits.append(False)
                continue
            hits.append(bool(M >= 1.4 * previo.min() and b[k] <= 0.85 * M and sma4[k] < sma13[k]))
        res["credito"] = any(hits)
        cond = (baa_w / baa_w.shift(13) - 1 >= 0.30) & ((baa_w - baa_w.shift(4)) > (baa_w.shift(4) - baa_w.shift(8)))
        res["veto"] = bool(cond.iloc[-4:].any())
        res["baa_13s"] = (baa_w.iloc[-1] / baa_w.iloc[-14] - 1) * 100
    if ratio_vix_w is not None and len(ratio_vix_w) > 40:
        r = ratio_vix_w.values
        n = len(r)
        hits = []
        for k in range(n - V, n):
            if k < 15:
                continue
            hits.append(bool(r[k - 1] < 0.95 and r[k] < 0.95 and (r[max(0, k - 14):k - 1] > 1).any()))
        res["vix"] = any(hits)
        res["ratio_vix"] = float(r[-1])
    return res


def puerta_indice(px_w, comun):
    """Puntos de la puerta para un índice: su media de 200 semanas y su RSI + condiciones comunes."""
    res = {"ma200": None, "rsi": None, "dist": None, "rsi_v": None, "puntos": 0, "tramo": 0}
    V = V_MEMORIA
    if px_w is not None and len(px_w) > 210:
        ma = px_w.rolling(200).mean()
        res["ma200"] = bool((px_w <= 1.05 * ma).iloc[-V:].any())
        res["dist"] = (px_w.iloc[-1] / ma.iloc[-1] - 1) * 100
    if px_w is not None and len(px_w) > 30:
        rr = rsi(px_w)
        res["rsi"] = bool((rr.iloc[-V:] < 30).any())
        res["rsi_v"] = float(rr.iloc[-1])
    pts = 2 * bool(res["ma200"]) + 2 * bool(comun["credito"]) + bool(comun["vix"]) + bool(res["rsi"])
    res["puntos"] = int(pts)
    tramo = 3 if pts >= 6 else 2 if pts >= 4 else 1 if pts >= 2 else 0
    res["tramo"] = min(tramo, 1) if comun["veto"] else tramo
    return res


NIVELES = ["MUY MALAS", "MALAS", "REGULAR-PESIMISTA", "REGULAR-OPTIMISTA", "BUENAS", "MUY BUENAS"]
COLOR_NIVEL = {"MUY MALAS": "#8e1b1b", "MALAS": "#d9534f", "REGULAR-PESIMISTA": "#e0a33a",
               "REGULAR-OPTIMISTA": "#9cbf4a", "BUENAS": "#2e9d55", "MUY BUENAS": "#146b37"}


def condicion_indice(px_w, ref_w, comun, ecy, es_eeuu):
    if px_w is None or len(px_w) < 60:
        return None
    ma40 = px_w.rolling(40).mean()
    pendiente = ma40.iloc[-1] / ma40.iloc[-5] - 1
    cierre = px_w.iloc[-1]
    if cierre > ma40.iloc[-1] and pendiente > 0:
        tend = "alcista"
    elif cierre < ma40.iloc[-1] and pendiente < 0:
        tend = "bajista"
    else:
        tend = "lateral"
    fase, extra, Yv = None, {"alc": 0}, 100.0
    if ref_w is not None:
        rs, X, Y = rrg(px_w, ref_w)
        XY = pd.concat([X, Y], axis=1).dropna()
        if len(XY) >= 12:
            fase, extra = fase_motor(px_w.loc[:rs.index[-1]], rs, XY.iloc[:, 0], XY.iloc[:, 1])
            Yv = float(XY.iloc[-1, 1])
    pu = puerta_indice(px_w, comun)
    dist = pu["dist"]
    lejos = dist is None or dist > 10
    valor_ok = (not es_eeuu) or (ecy is not None and ecy >= 3)
    atractivo = (not lejos) and valor_ok
    if tend == "bajista":
        if comun["veto"]:
            n, m = "MUY MALAS", "Tendencia bajista confirmada y crédito deteriorándose con rapidez"
        elif lejos:
            n, m = "MUY MALAS", "Tendencia bajista confirmada y precio aún lejos de su media de 200 semanas"
        elif pu["tramo"] >= 2:
            n, m = "REGULAR-OPTIMISTA", "Bajista, pero cerca de la zona de suelo y con señales de suelo acumulándose"
        else:
            n, m = "REGULAR-PESIMISTA", "Bajista, cerca de la zona de suelo: esperar señales"
    elif tend == "alcista":
        if fase in ("MADUREZ", "GIRO") or extra.get("alc", 0) >= 2:
            n, m = "MALAS", f"Tendencia alcista con señales de fin de ciclo (fase {fase.lower() if fase else 'sin dato'})"
        elif fase in ("REZAGADO", "SUELO"):
            n, m = "REGULAR-OPTIMISTA", "Tendencia alcista, pero perdiendo fuerza frente a la liquidez"
        elif atractivo:
            n, m = "MUY BUENAS", "Tendencia alcista sana y precio atractivo"
        else:
            n, m = "BUENAS", "Tendencia alcista sana"
            if lejos:
                m += ", aunque el precio está lejos de su media de 200 semanas"
    else:
        if lejos and fase in ("GIRO", "REZAGADO"):
            n, m = "MALAS", "Sin tendencia, impulso débil y lejos de la zona de suelo"
        elif fase in ("ARRANQUE", "SUELO") or Yv >= 100:
            n, m = "REGULAR-OPTIMISTA", "Sin tendencia clara, pero el impulso mejora"
        else:
            n, m = "REGULAR-PESIMISTA", "Sin tendencia clara y con el impulso débil"
    if es_eeuu and ecy is not None and ecy < 1.5 and n in ("BUENAS", "MUY BUENAS"):
        m += " · valoración muy exigente (ECY bajo)"
    return {"nivel": n, "motivo": m, "tend": tend, "fase": fase, "dist": dist,
            "puntos": pu["puntos"], "tramo": pu["tramo"], "pu": pu}


# ----------------------------------------------------------------------------
# CALENDARIO
# ----------------------------------------------------------------------------

def tercer_viernes(anio, mes):
    d = dt.date(anio, mes, 1)
    primero = d + dt.timedelta(days=(4 - d.weekday()) % 7)
    return primero + dt.timedelta(days=14)


def ultimo_viernes(anio, mes):
    d = dt.date(anio + (mes // 12), mes % 12 + 1, 1) - dt.timedelta(days=1)
    return d - dt.timedelta(days=(d.weekday() - 4) % 7)


def segundo_viernes(anio, mes):
    d = dt.date(anio, mes, 1)
    return d + dt.timedelta(days=(4 - d.weekday()) % 7 + 7)


def ultimo_habil(anio, mes):
    d = dt.date(anio + (mes // 12), mes % 12 + 1, 1) - dt.timedelta(days=1)
    while d.weekday() > 4:
        d -= dt.timedelta(days=1)
    return d


def calendario(dias=120):
    hoy = HOY.date()
    fin = hoy + dt.timedelta(days=dias)
    ev = []
    for anio in range(hoy.year, fin.year + 1):
        for mes in range(1, 13):
            tv = tercer_viernes(anio, mes)
            if mes in (3, 6, 9, 12):
                ev.append((tv, "Vencimiento trimestral de opciones y futuros + rebalanceo del S&P 500", "Opciones"))
            else:
                ev.append((tv, "Vencimiento mensual de opciones", "Opciones"))
            sig = (anio + (mes // 12), mes % 12 + 1)
            vix = tercer_viernes(*sig) - dt.timedelta(days=30)
            ev.append((vix, "Vencimiento del VIX", "Opciones"))
            fm = ultimo_habil(anio, mes)
            if mes == 12:
                ev.append((fm, "Cierre de año (cierre de libros)", "Cierres"))
            elif mes in (3, 6, 9):
                ev.append((fm, "Cierre de trimestre (cierre de libros, rebalanceo de fondos de pensiones)", "Cierres"))
            # Blackout estimado de recompras
            if mes in (3, 6, 9, 12):
                fin_t = dt.date(anio + (mes // 12), mes % 12 + 1, 1) - dt.timedelta(days=1)
                ini_b = fin_t - dt.timedelta(days=20)
                fin_b = fin_t + dt.timedelta(days=24)
                ev.append((ini_b, "Empieza el blackout de recompras (estimado)", "Recompras"))
                ev.append((fin_b, "Termina el blackout de recompras (estimado)", "Recompras"))
        ev.append((ultimo_viernes(anio, 6), "Reconstitución Russell (junio), efectiva al cierre", "Índices"))
        ev.append((segundo_viernes(anio, 12), "Reconstitución Russell (diciembre), efectiva al cierre", "Índices"))
        ev.append((ultimo_habil(anio, 4), "Russell: día de referencia (junio)", "Índices"))
        ev.append((ultimo_habil(anio, 10), "Russell: día de referencia (diciembre)", "Índices"))
        ev.append((dt.date(anio, 3, 31), "Fin del año fiscal en Japón", "Fiscal"))
        ev.append((dt.date(anio, 4, 15), "Día de impuestos en EE. UU.", "Fiscal"))
        ev.append((dt.date(anio, 10, 31), "Fin del ejercicio fiscal de muchos fondos estadounidenses", "Fiscal"))
        ev.append((dt.date(anio, 12, 1), "Empieza la ventana de ventas por pérdidas fiscales", "Fiscal"))
    for a, b in FOMC:
        ev.append((dt.date.fromisoformat(b), f"Reunión de la Fed (FOMC): decisión; empieza el {a[8:]}-{a[5:7]}", "Fed"))
    for f in IPC_EEUU:
        ev.append((dt.date.fromisoformat(f), "IPC EE. UU.", "Datos"))
    ev = sorted({(d, t, c) for d, t, c in ev if hoy <= d <= fin})
    # ¿Estamos en blackout estimado?
    en_blackout = False
    for anio in (hoy.year - 1, hoy.year):
        for mes in (3, 6, 9, 12):
            fin_t = dt.date(anio + (mes // 12), mes % 12 + 1, 1) - dt.timedelta(days=1)
            if fin_t - dt.timedelta(days=20) <= hoy <= fin_t + dt.timedelta(days=24):
                en_blackout = True
    return ev, en_blackout


def estacionalidad(sh, spy_diario):
    out = {}
    if sh is not None:
        p = sh["P"].dropna()
        ret = p.pct_change().dropna() * 100
        tabla = pd.DataFrame({"r": ret.values, "mes": ret.index.month})
        g = tabla.groupby("mes")["r"]
        out["meses"] = pd.DataFrame({"media": g.mean(), "mediana": g.median(),
                                     "positivos": g.apply(lambda x: (x > 0).mean() * 100)})
        anual = p.resample("YS").last().pct_change().dropna() * 100
        anual = anual[anual.index.year < HOY.year]
        ciclo = pd.DataFrame({"r": anual.values, "c": anual.index.year % 4})
        nombres = {0: "Año electoral", 1: "Año 1 tras elecciones", 2: "Año de medio mandato", 3: "Año preelectoral"}
        gc = ciclo.groupby("c")["r"]
        out["ciclo"] = [(nombres[k], gc.mean()[k], gc.median()[k], (ciclo[ciclo.c == k].r > 0).mean() * 100)
                        for k in (1, 2, 3, 0)]
        out["anio_actual"] = nombres[HOY.year % 4]
        out["desde"] = p.index[0].year
    if spy_diario is not None and len(spy_diario) > 500:
        r = spy_diario.pct_change().dropna() * 100
        df = pd.DataFrame({"r": r})
        df["ym"] = df.index.to_period("M")
        df["n"] = df.groupby("ym").cumcount()
        df["m"] = df.groupby("ym")["r"].transform("count") - df["n"]
        cambio = (df["m"] <= 1) | (df["n"] <= 2)
        out["cambio_mes"] = (df[cambio]["r"].mean(), df[~cambio]["r"].mean(), spy_diario.index[0].year)
    return out


# ----------------------------------------------------------------------------
# GRÁFICOS SVG
# ----------------------------------------------------------------------------

def svg_linea(serie, alto=120, ancho=680, color="#2f6fdb", ref=None):
    s = serie.dropna()
    if len(s) < 2:
        return ""
    v = s.values
    lo, hi = float(np.min(v)), float(np.max(v))
    if ref is not None:
        lo, hi = min(lo, ref), max(hi, ref)
    if hi == lo:
        hi = lo + 1
    m = 6
    pts = " ".join(f"{m + i / (len(v) - 1) * (ancho - 2 * m):.1f},{alto - m - (x - lo) / (hi - lo) * (alto - 2 * m):.1f}"
                   for i, x in enumerate(v))
    out = [f'<svg viewBox="0 0 {ancho} {alto}" class="mini">']
    if ref is not None:
        yr = alto - m - (ref - lo) / (hi - lo) * (alto - 2 * m)
        out.append(f'<line x1="0" x2="{ancho}" y1="{yr:.1f}" y2="{yr:.1f}" stroke="#bbb" stroke-dasharray="4 4"/>')
    out.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.8"/></svg>')
    return "".join(out)


# ----------------------------------------------------------------------------
# EXPLICACIONES DE LOS MAPAS (texto al pulsar sobre un vector)
# ----------------------------------------------------------------------------

CUAD_MOTOR = ("LIDERANDO", "DEBILITÁNDOSE", "REZAGADO", "MEJORANDO")
CUAD_MACRO = ("ALTO Y SUBIENDO", "ALTO Y BAJANDO", "BAJO Y BAJANDO", "BAJO Y SUBIENDO")

# (cuadrante actual, ¿impulso subiendo en 4 semanas?) -> (tendencia, resumen en lenguaje llano)
VEREDICTO_MOTOR = {
    ("LIDERANDO", True): ("ALCISTA Y ACELERANDO",
                          "Lo está haciendo mejor que la liquidez y la ventaja se amplía."),
    ("LIDERANDO", False): ("ALCISTA, PERDIENDO EMPUJE",
                           "Sigue haciéndolo mejor que la liquidez, pero la ventaja crece cada vez más despacio."),
    ("DEBILITÁNDOSE", False): ("ALCISTA AGOTÁNDOSE",
                               "Ha ido mejor que la liquidez, pero ahora devuelve parte de esa ventaja semana a semana."),
    ("DEBILITÁNDOSE", True): ("PAUSA DENTRO DE UNA SUBIDA",
                              "Ha cedido parte de su ventaja, pero en el último mes vuelve a coger fuerza."),
    ("REZAGADO", False): ("BAJISTA Y EMPEORANDO",
                          "Lo está haciendo peor que la liquidez y cada vez peor."),
    ("REZAGADO", True): ("BAJISTA, PERO FRENANDO",
                         "Sigue haciéndolo peor que la liquidez, pero la caída relativa se frena."),
    ("MEJORANDO", True): ("ALCISTA EN FORMACIÓN",
                          "Todavía va por detrás de la liquidez, pero está recuperando terreno con fuerza."),
    ("MEJORANDO", False): ("MEJORA QUE PIERDE FUELLE",
                           "Está recuperando terreno frente a la liquidez, pero cada vez más despacio."),
}

TRAMO_MOTOR = {"LIDERANDO": "por delante de la liquidez y ganando ventaja",
               "DEBILITÁNDOSE": "por delante de la liquidez, pero perdiendo ventaja",
               "REZAGADO": "por detrás de la liquidez y perdiendo terreno",
               "MEJORANDO": "por detrás de la liquidez, pero recuperando"}
TRAMO_MACRO = {"ALTO Y SUBIENDO": "por encima de su nivel típico y subiendo más deprisa de lo habitual",
               "ALTO Y BAJANDO": "por encima de su nivel típico, pero bajando o subiendo menos de lo habitual",
               "BAJO Y BAJANDO": "por debajo de su nivel típico y bajando o subiendo menos de lo habitual",
               "BAJO Y SUBIENDO": "por debajo de su nivel típico, pero subiendo más deprisa de lo habitual"}

QUE_HACER = {
    "SUELO": "Vigilar: puede estar formando suelo, pero aún no hay confirmación.",
    "ARRANQUE": "Construir poco a poco: es el principio del ciclo, donde está el margen, pero muchos arranques fallan.",
    "ACELERACIÓN": "Mantener: la tendencia está viva.",
    "MADUREZ": "Empezar a salir: la subida da señales de cansancio antes del clímax.",
    "GIRO": "Salir: la ventaja frente a la liquidez se está perdiendo.",
    "REZAGADO": "No tocar: va peor que la liquidez y no hay señales de suelo.",
}

# Qué suele significar para el mercado que cada motor vaya mejor (fuerte) o peor (débil) que la liquidez.
LECTURA_MOTOR = {
    "ACWI": ("El mercado premia tener acciones de todo el mundo frente a tener liquidez: apetito por el riesgo.",
             "La bolsa mundial pierde atractivo frente a la liquidez: el mercado está reduciendo riesgo."),
    "SPY": ("Apetito por la bolsa de EE. UU., sobre todo por las grandes empresas que más pesan en el índice.",
            "La bolsa de EE. UU. pierde atractivo frente a la liquidez: el mercado reduce riesgo o duda de las grandes empresas."),
    "RSP": ("La subida es amplia: participan muchas empresas, no solo las grandes. Suele ser señal de mercado sano.",
            "La empresa media de EE. UU. va peor que la liquidez: o el mercado baja, o la subida es estrecha y depende de pocas grandes compañías."),
    "VGK": ("El dinero busca Europa: suele coincidir con un euro firme, mejora del ciclo europeo o salida de dinero desde EE. UU.",
            "Europa pierde atractivo: el dinero prefiere otros mercados o la liquidez."),
    "EEM": ("Apetito por los emergentes: suele coincidir con dólar débil, materias primas firmes y crecimiento global.",
            "Los emergentes pierden: suele coincidir con dólar fuerte, tipos altos en EE. UU. o miedo al crecimiento global."),
    "QQQ": ("Apetito por la tecnología: suele coincidir con tipos a la baja o entusiasmo por los beneficios del sector.",
            "La tecnología pierde fuerza: suele coincidir con tipos al alza o dudas sobre sus valoraciones."),
    "IWM": ("Las empresas pequeñas lideran: el mercado apuesta por la economía de EE. UU. y por tipos más bajos (dependen más del crédito).",
            "Las pequeñas sufren: dudas sobre la economía de EE. UU. o crédito caro."),
    "VTV": ("El estilo value lidera: suele darse con tipos o inflación al alza y con rotación desde la tecnología.",
            "Value pierde terreno: el mercado prefiere crecimiento o refugio."),
    "VUG": ("El crecimiento lidera: suele darse con tipos a la baja y confianza en los beneficios futuros.",
            "Growth pierde terreno: suele coincidir con tipos al alza o recogida de beneficios en las grandes tecnológicas."),
    "MTUM": ("Las tendencias del mercado se mantienen: lo que más ha subido sigue subiendo.",
             "El momentum falla: suele indicar un cambio de liderazgo en el mercado."),
    "QUAL": ("El mercado prefiere empresas sólidas y poco endeudadas: actitud prudente, típica de fases maduras del ciclo.",
             "La calidad queda atrás: el mercado busca más riesgo (inicio de ciclo) o todo cae a la vez."),
    "BTAL": ("Las acciones defensivas baten a las arriesgadas: suele coincidir con caídas de la bolsa o con miedo.",
             "Las acciones arriesgadas baten a las defensivas: mercado con apetito por el riesgo."),
    "TLT": ("Los bonos largos suben, es decir, sus tipos bajan: el mercado descuenta bajadas de tipos, menos inflación o busca refugio.",
            "Los bonos largos caen, es decir, sus tipos suben: presión de inflación, de déficit público o de tipos altos durante más tiempo. "
            "Tipos largos al alza también pesan sobre la bolsa cara."),
    "IEF": ("Los bonos a 7-10 años suben: el mercado espera tipos más bajos o busca refugio.",
            "Los bonos a 7-10 años caen: el mercado espera tipos altos durante más tiempo."),
    "TIP": ("Los bonos ligados a la inflación lideran: el mercado se protege frente a la inflación o espera tipos reales más bajos.",
            "Los bonos ligados a la inflación pierden: tipos reales al alza o menos miedo a la inflación."),
    "HYG": ("El crédito de peor calidad va bien: confianza en las empresas y poco miedo a impagos. Suele acompañar a una bolsa alcista.",
            "El crédito de peor calidad sufre: el mercado teme impagos. Históricamente suele anticipar o acompañar caídas de la bolsa."),
    "DBC": ("Las materias primas lideran: presión inflacionista o demanda global fuerte.",
            "Las materias primas pierden: menos inflación o miedo a la demanda global."),
    "XLE": ("La energía lidera: petróleo y gas al alza, presión inflacionista.",
            "La energía pierde: petróleo débil y menos presión de precios."),
    "GLD": ("El oro lidera: demanda de refugio, desconfianza en las divisas o expectativa de tipos reales más bajos.",
            "El oro pierde frente a la liquidez: tipos reales altos o menos miedo."),
    "URA": ("El uranio lidera: entra dinero en un tema estructural (energía nuclear), muy volátil.",
            "El uranio pierde fuerza: sale dinero de un tema muy volátil."),
    "UUP": ("Dólar fuerte: endurece las condiciones financieras globales y suele perjudicar a emergentes y materias primas. "
            "Para un inversor en euros, sube el valor de lo que tiene en dólares.",
            "Dólar débil: suele favorecer a emergentes y materias primas. Para un inversor en euros, resta rentabilidad a lo que tiene en dólares."),
    "FXE": ("Euro fuerte frente al dólar: para un inversor en euros, lo que tiene en dólares rinde menos.",
            "Euro débil frente al dólar: para un inversor en euros, lo que tiene en dólares rinde más."),
    "DBMF": ("Las estrategias de tendencia ganan: hay tendencias claras y duraderas en tipos, divisas o materias primas, al alza o a la baja.",
             "Las estrategias de tendencia sufren: mercados sin dirección o con giros bruscos."),
    "VIXY": ("La volatilidad sube: aumenta el miedo en el mercado.",
             "La volatilidad cae o está dormida: mercado tranquilo (VIXY pierde valor con el tiempo en esa situación)."),
}


def cuadrante(x, y, centro, nombres):
    if x >= centro:
        return nombres[0] if y >= centro else nombres[1]
    return nombres[3] if y >= centro else nombres[2]


def _tramos(qs):
    """Agrupa la secuencia de cuadrantes en tramos [cuadrante, inicio, duración].
    Una escapada de un solo periodo entre dos tramos del mismo cuadrante se considera ruido."""
    runs = []
    for k, q in enumerate(qs):
        if runs and runs[-1][0] == q:
            runs[-1][2] += 1
        else:
            runs.append([q, k, 1])
    j = 1
    while j < len(runs) - 1:
        if runs[j][2] == 1 and runs[j - 1][0] == runs[j + 1][0]:
            runs[j - 1][2] += 1 + runs[j + 1][2]
            del runs[j:j + 2]
            j = max(1, j - 1)
        else:
            j += 1
    return runs


def _recorrido(runs, fechas, fmt_f, sg, pl, nombres, frases, mensual=False):
    """Lista en lenguaje llano por dónde ha pasado, con fechas, y si ha seguido el ciclo normal."""
    def u(L):
        return f"{L} {sg if L == 1 else pl}"
    items = []
    for q, k, L in runs:
        fin = fechas[k + L - 1]
        if k + L == len(fechas):
            tramo = f"{'Desde' if mensual else 'Desde el'} {fd(fmt_f(fechas[k]))}"
        elif L == 1:
            tramo = f"{'Mes de' if mensual else 'Semana del'} {fd(fmt_f(fechas[k]))}"
        else:
            tramo = f"{fd(fmt_f(fechas[k]))} → {fd(fmt_f(fin))}"
        items.append(f"<li>{tramo} ({u(L)}): {frases[q]}.</li>")
    txt = "<ul class=\"exp-lista\">" + "".join(items) + "</ul>"
    if len(runs) >= 3:
        ciclo = [nombres[3], nombres[0], nombres[1], nombres[2]]
        h = a = 0
        for (q1, _, _), (q2, _, _) in zip(runs[:-1], runs[1:]):
            d = (ciclo.index(q2) - ciclo.index(q1)) % 4
            h += d == 1
            a += d != 1
        if a == 0:
            txt += "<p>Ha seguido el orden normal del ciclo, sin pasos atrás.</p>"
        elif a >= h:
            txt += "<p>Ha ido y venido sin un orden claro: la tendencia es poco fiable.</p>"
        else:
            txt += "<p>En general ha seguido el orden normal del ciclo, con algún paso atrás.</p>"
    return txt


def _con_signo(x, dec=1, suf=" %"):
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return "—"
    return ("+" if x >= 0 else "−") + fmt(abs(x), dec, suf)


def narrativa_motor(m):
    """Texto en lenguaje de mercado con la dinámica de un motor en el mapa de rotación."""
    t, xs, ys, fechas = m["t"], m["xs"], m["ys"], m["fechas"]
    n = len(xs)
    if n < 6:
        return ""
    f_ = (lambda d: f"{d:%d-%m-%Y}")
    qs = [cuadrante(x, y, 100, CUAD_MOTOR) for x, y in zip(xs, ys)]
    runs = _tramos(qs)
    q = qs[-1]
    dx, dy = xs[-1] - xs[-5], ys[-1] - ys[-5]
    tit, resumen = VEREDICTO_MOTOR[(q, dy > 0)]
    color = COLOR_GRUPO.get(GRUPO.get(t, ""), "#555")
    alternativo = GRUPO.get(t) in EXCLUIR_TOP
    P = [f'<h3><span class="chip" style="background:{color}"></span>{e(t)} · {e(m["nombre"])}</h3>',
         f'<p class="exp-ver"><b>En pocas palabras.</b> {resumen}<br>Tendencia frente a la liquidez: <b>{tit}</b>.</p>']
    # Qué dice del mercado
    fuerte, debil = LECTURA_MOTOR.get(t, ("", ""))
    if fuerte:
        if q in ("LIDERANDO",):
            lec = fuerte
        elif q == "REZAGADO":
            lec = debil
        elif q == "MEJORANDO":
            lec = f"{debil} <i>Si la recuperación continúa:</i> {fuerte[0].lower() + fuerte[1:]}"
        else:
            lec = f"{fuerte} <i>Si sigue perdiendo ventaja:</i> {debil[0].lower() + debil[1:]}"
        P.append(f'<p><span class="et">Qué dice del mercado</span> {lec}</p>')
    # Qué hacer
    hacer = ("Solo referencia: es una cobertura o una estrategia, no un motor que se rote por tendencia."
             if alternativo else QUE_HACER[m["fase"]])
    avisos = []
    if math.hypot(xs[-1] - 100, ys[-1] - 100) < 1.0:
        avisos.append("Está muy cerca del punto neutro: señal débil, puede cambiar de situación con poco movimiento.")
    elif math.hypot(dx, dy) < 0.4:
        avisos.append("Casi sin movimiento en el último mes: señal débil.")
    if m.get("s_alc"):
        avisos.append(f"Señales de cansancio de la subida ({len(m['s_alc'])} de 4): {'; '.join(m['s_alc'])}.")
    if m.get("s_baj"):
        avisos.append(f"Señales de agotamiento de la caída ({len(m['s_baj'])} de 4): {'; '.join(m['s_baj'])}.")
    P.append(f'<p><span class="et">Qué hacer según las reglas</span> Fase {m["fase"].lower()}. {hacer}</p>')
    if avisos:
        P.append('<p class="exp-aviso">' + "<br>".join(avisos) + "</p>")
    # Rentabilidades
    rents = m.get("rents") or {}
    if rents:
        nombres_p = {4: "Último mes", 13: "3 meses", 26: "6 meses"}
        filas = "".join(f"<tr><td>{nombres_p[k]}</td><td>{_con_signo(a)}</td><td>{_con_signo(b)}</td>"
                        f"<td><b>{_con_signo(a - b, 1, '')}</b></td></tr>" for k, (a, b) in sorted(rents.items()))
        P.append(f'<p><span class="et">Rentabilidad hasta el {fd(f_(fechas[-1]))}</span></p>'
                 f'<table class="exp-tabla"><tr><th></th><th>{e(t)}</th><th>Liquidez (BIL)</th><th>Diferencia (puntos)</th></tr>{filas}</table>')
    # Recorrido
    P.append(f'<p><span class="et">Cómo ha llegado hasta aquí (últimas 24 semanas)</span></p>'
             + _recorrido(runs, fechas, f_, "semana", "semanas", CUAD_MOTOR, TRAMO_MOTOR))
    if t in NOTA_ALTERNATIVO:
        P.append(f'<p class="nota">{NOTA_ALTERNATIVO[t]}</p>')
    P.append(f'<p class="nota">"Por delante" o "por detrás" de la liquidez compara su evolución reciente frente a las letras del Tesoro '
             f'con su media de los últimos 6 meses. Fuerza ahora {fmt(xs[-1], 1)} (100 = esa media); impulso {fmt(ys[-1], 1)} '
             f'(100 = sin cambio en 4 semanas; hace 4 semanas, {fmt(ys[-5], 1)}).</p>')
    return "".join(P)


NOTA_ALTERNATIVO = {
    "VIXY": "En la volatilidad, una tendencia alcista significa que aumenta el miedo en el mercado.",
    "BTAL": "En el anti-beta, una tendencia alcista suele coincidir con caídas de la bolsa o con rotación hacia valores defensivos.",
}


# ----------------------------------------------------------------------------
# RÉGIMEN MACRO (crecimiento × inflación) y lectura de cada variable
# ----------------------------------------------------------------------------

REGIMENES = {
    (True, False): ("RECUPERACIÓN", "crecimiento mejorando e inflación bajando",
                    "Históricamente, el mejor entorno para la bolsa. Los bonos aguantan.",
                    "Favorece: bolsa, sobre todo cíclica; crédito. Penaliza: poco."),
    (True, True): ("SOBRECALENTAMIENTO", "crecimiento mejorando e inflación subiendo",
                   "Suelen ir mejor las materias primas y la energía; los bonos largos sufren. Aquí las buenas noticias económicas "
                   "pueden ser malas para el mercado: alejan las bajadas de tipos.",
                   "Favorece: materias primas, energía, value. Penaliza: bonos largos; bolsa cara si suben los tipos."),
    (False, True): ("ESTANFLACIÓN", "crecimiento empeorando e inflación subiendo",
                    "El peor entorno para la bolsa y los bonos a la vez. Históricamente aguantan mejor la liquidez, las materias primas, "
                    "el oro y las estrategias de tendencia.",
                    "Favorece: liquidez, materias primas, oro, tendencia (managed futures). Penaliza: bolsa y bonos."),
    (False, False): ("DESACELERACIÓN", "crecimiento empeorando e inflación bajando",
                     "Suelen ir mejor los bonos del Tesoro. Al principio, los datos económicos flojos pueden ser buenos para la bolsa "
                     "porque anticipan bajadas de tipos; si la economía entra en recesión, dejan de serlo.",
                     "Favorece: bonos del Tesoro, calidad. Penaliza: materias primas; bolsa cíclica si llega la recesión."),
}
INFLACION_ALTA = 3.0   # % interanual: por encima, la Fed tiene poco margen (objetivo 2 %). Criterio.


def regimen_macro(mac, clave):
    inf, cre = mac.get(clave[0]), mac.get(clave[1])
    if not inf or not cre:
        return None
    sube_c, sube_i = cre["impulso"] > 0, inf["impulso"] > 0
    nom, desc, lectura, favor = REGIMENES[(sube_c, sube_i)]
    return {"nombre": nom, "desc": desc, "lectura": lectura, "favor": favor, "sube_c": sube_c, "sube_i": sube_i,
            "inf": inf["valor"], "inf_alta": inf["valor"] >= INFLACION_ALTA, "cre": cre["valor"],
            "f_inf": inf["fecha"], "f_cre": cre["fecha"]}


def lectura_macro(corto, v, reg):
    """Qué suele significar para el mercado el movimiento actual de la variable, según el régimen."""
    sube = v["impulso"] > 0
    niv = v["nivel"]
    if corto == "Inflación":
        if sube:
            t = "Malo para los bonos: más inflación suele traer tipos más altos durante más tiempo."
            if v["valor"] >= INFLACION_ALTA:
                t += (f" Con la inflación en {fmt(v['valor'], 1)} %, por encima del objetivo del 2 % de la Fed, hay poco margen para "
                      "bajar tipos, lo que también pesa sobre la bolsa cara.")
            t += " Históricamente favorece a materias primas, energía y estrategias de tendencia."
        else:
            t = "Favorable para los bonos y, si el crecimiento aguanta, para la bolsa: abre la puerta a bajadas de tipos."
            if v["valor"] >= INFLACION_ALTA:
                t += f" Aun así sigue en {fmt(v['valor'], 1)} %, por encima del objetivo del 2 %: la Fed puede tardar en reaccionar."
        return t
    if corto == "Crecimiento":
        zona = "en terreno de expansión (por encima de 0)" if v["valor"] > 0 else "en terreno de contracción (por debajo de 0)"
        if reg is None:
            return f"Las encuestas están {zona}."
        if sube and (reg["sube_i"] or reg["inf_alta"]):
            t = ("Buena noticia para los beneficios empresariales, pero con la inflación "
                 f"{'subiendo' if reg['sube_i'] else 'todavía alta'} puede ser mala noticia para el mercado: aleja las bajadas de tipos "
                 "y presiona a los bonos. Es el caso típico de «las buenas noticias son malas».")
        elif sube:
            t = "Favorable para la bolsa y el crédito: más beneficios sin presión de tipos."
        elif reg["sube_i"]:
            t = "Combinación de riesgo: la economía se enfría mientras la inflación sube (estanflación). Malo para la bolsa y los bonos a la vez."
        else:
            t = ("Mientras la economía siga en expansión, un enfriamiento puede ser buena noticia para la bolsa a corto plazo porque "
                 "anticipa bajadas de tipos: es el caso típico de «las malas noticias son buenas». Si deriva en recesión deja de serlo, "
                 "porque caen los beneficios. Favorable para los bonos del Tesoro.")
        return f"Las encuestas están {zona}. {t}"
    if corto == "Crédito (estrés)":
        if sube:
            t = ("El mercado exige más interés por prestar a empresas: crece el miedo a impagos. Suele anticipar o acompañar caídas "
                 "de la bolsa y del high yield.")
            if niv >= 80:
                t += " Además está en zona alta para su historia: estrés serio."
        else:
            t = "Menos miedo a impagos: entorno favorable para la bolsa y el crédito."
            if niv >= 80:
                t += (" El diferencial sigue alto pero ya baja: es la señal típica de que lo peor del estrés ha pasado "
                      "(condición «crédito girando» de la puerta).")
            elif niv <= 20:
                t += " Pero está muy bajo para su historia: complacencia, queda poco margen de mejora."
        return t
    if corto == "Liquidez":
        if sube:
            return ("La Fed deja más dinero en el sistema financiero: suele apoyar a los activos de riesgo. "
                    "Relación muy seguida por el mercado, pero con poca evidencia académica sólida.")
        return ("La Fed retira dinero del sistema financiero: viento en contra para los activos de riesgo. "
                "Relación muy seguida por el mercado, pero con poca evidencia académica sólida.")
    return ""


def _ritmo_hist(imp, p):
    if imp > 0 and p >= 90:
        return "de las subidas más fuertes de su historia"
    if imp > 0 and p >= 70:
        return "subida mayor de lo habitual"
    if imp < 0 and p <= 10:
        return "de las bajadas más fuertes de su historia"
    if imp < 0 and p <= 30:
        return "bajada mayor de lo habitual"
    return "ritmo dentro de lo habitual"


UNIDAD_MACRO = {"Inflación": " %", "Crecimiento": "", "Crédito (estrés)": " puntos", "Liquidez": " billones $"}


def narrativa_macro(corto, v, color, reg):
    """Texto en lenguaje de mercado con la dinámica de una variable del mapa macro."""
    fechas, n = v["fechas"], len(v["rx"])
    if n < 2:
        return ""
    mensual = v["frecuencia"] == "m"
    f_ = (lambda d: f"{d:%m-%Y}") if mensual else (lambda d: f"{d:%d-%m-%Y}")
    sg, pl = ("mes", "meses") if mensual else ("semana", "semanas")
    qs = [cuadrante(x, y, 50, CUAD_MACRO) for x, y in zip(v["rx"], v["ry"])]
    runs = _tramos(qs)
    imp = v["impulso"]
    if imp > 0:
        tit = ("SUBIENDO CADA VEZ MÁS DEPRISA" if v["acelera3"] else
               "SUBIENDO, PERO CADA VEZ MÁS DESPACIO" if v["frenando3"] else "SUBIENDO")
    elif imp < 0:
        tit = ("BAJANDO CADA VEZ MÁS DEPRISA" if v["acelera3"] else
               "BAJANDO, PERO CADA VEZ MÁS DESPACIO" if v["frenando3"] else "BAJANDO")
    else:
        tit = "SIN CAMBIOS"
    dec = 0 if abs(v["valor"]) >= 1000 else 2
    uni = UNIDAD_MACRO.get(corto, "")
    per = f'{v["periodos"]} {"meses" if mensual else "semanas"}'
    cambio = _con_signo(imp, 2, " %" if v["modo"] == "pct" else " puntos")
    niv = v["nivel"]
    pista = ""
    if niv >= 80 and imp > 0 and v["frenando3"]:
        pista = " Alta y subiendo cada vez más despacio: es lo típico cerca de un techo."
    elif niv <= 20 and imp < 0 and v["frenando3"]:
        pista = " Baja y cayendo cada vez más despacio: es lo típico cerca de un suelo."
    P = [f'<h3><span class="chip" style="background:{color}"></span>{e(corto)}</h3>',
         f'<p class="exp-ver"><b>En pocas palabras.</b> Está en {fmt(v["valor"], dec)}{uni} ({fd(f_(v["fecha"]))}) y '
         f'{"sube" if imp > 0 else "baja" if imp < 0 else "no cambia"}: {cambio} en {per}, {_ritmo_hist(imp, v["nivel_imp"])}.{pista}'
         f'<br>Tendencia: <b>{tit}</b>.</p>',
         f'<p><span class="et">Qué significa para el mercado</span> {lectura_macro(corto, v, reg)}</p>',
         f'<p><span class="et">Frente a su historia</span> Percentil {fmt(niv, 0)} de toda su historia (desde {fd(v["desde"])}): '
         f'más alta que el {fmt(niv, 0)} % de los datos desde entonces. Hace 24 semanas estaba en {fmt(v["vals"][0], dec)}{uni} '
         f'(percentil {fmt(v["rx"][0], 0)}).</p>',
         f'<p><span class="et">Cómo ha llegado hasta aquí (últimas 24 semanas)</span></p>'
         + _recorrido(runs, fechas, f_, sg, pl, CUAD_MACRO, TRAMO_MACRO, mensual),
         f'<p class="nota">Fase según las reglas: {v["fase"].lower()}. "Nivel típico" = su mediana histórica (percentil 50). '
         f'{"Dato mensual: un punto por mes." if mensual else "Cierres semanales."}</p>']
    return "".join(P)


# ----------------------------------------------------------------------------
# PROGRAMA PRINCIPAL
# ----------------------------------------------------------------------------

def main():
    os.makedirs(CARPETA_WEB, exist_ok=True)
    open(os.path.join(CARPETA_WEB, ".nojekyll"), "w").close()

    # --- Descargas
    precios = {}
    for t in [m for m, _ in MOTORES] + [REFERENCIA] + OTROS_TIINGO:
        s = seguro(f"Precio {t}", tiingo, t, "daily" if t == "SPY" else "weekly")
        if s is not None:
            precios[t] = s
            registrar(f"Precio {t}", s[s.index <= HOY])
        time.sleep(0.5)
    fr = {}
    for sid in FRED_SERIES:
        s = seguro(FRED_SERIES[sid], fred, sid)
        if s is not None:
            fr[sid] = s
            registrar(FRED_SERIES[sid], s)
    cb = {}
    for n in CBOE_INDICES:
        s = seguro(f"Cboe {n}", cboe_indice, n)
        if s is not None:
            cb[n] = s
            registrar(f"Cboe {n}", s)
    sh = seguro("CAPE / ECY (Shiller)", shiller)
    if sh is not None:
        registrar("CAPE / ECY (Shiller)", sh["CAPE"].dropna())
    fci = seguro("FCI-G (Reserva Federal)", fcig)
    registrar("FCI-G (Reserva Federal)", fci)
    pc = seguro("Ratio put/call (Cboe)", actualizar_putcall)
    if pc is not None:
        registrar("Ratio put/call (Cboe)", pc)
    spy_ult = float(precios["SPY"].iloc[-1]) if "SPY" in precios else None
    gm = seguro("Gamma SPX (Cboe)", gamma_spx, spy_ult)
    cot_series = {}
    for etq, ds, cod, l, c in [
        ("S&P 500 · gestoras", "gpe5-46if", "13874A", "asset_mgr_positions_long", "asset_mgr_positions_short"),
        ("S&P 500 · fondos apalancados", "gpe5-46if", "13874A", "lev_money_positions_long", "lev_money_positions_short"),
        ("Nasdaq 100 · gestoras", "gpe5-46if", "209742", "asset_mgr_positions_long", "asset_mgr_positions_short"),
        ("Nasdaq 100 · fondos apalancados", "gpe5-46if", "209742", "lev_money_positions_long", "lev_money_positions_short"),
        ("VIX · fondos apalancados", "gpe5-46if", "1170E1", "lev_money_positions_long", "lev_money_positions_short"),
        ("VIX · gestoras", "gpe5-46if", "1170E1", "asset_mgr_positions_long", "asset_mgr_positions_short"),
        ("Bono 10 años · fondos apalancados", "gpe5-46if", "043602", "lev_money_positions_long", "lev_money_positions_short"),
        ("Bono 10 años · gestoras", "gpe5-46if", "043602", "asset_mgr_positions_long", "asset_mgr_positions_short"),
        ("Oro · especuladores", "72hh-3qpy", "088691", "m_money_positions_long_all", "m_money_positions_short_all"),
        ("Petróleo WTI · especuladores", "72hh-3qpy", "067651", "m_money_positions_long_all", "m_money_positions_short_all"),
        ("Cobre · especuladores", "72hh-3qpy", "085692", "m_money_positions_long_all", "m_money_positions_short_all"),
    ]:
        s = seguro(f"Posicionamiento {etq}", cot, ds, cod, l, c)
        if s is not None:
            cot_series[etq] = s
    if cot_series:
        registrar("Posicionamiento (CFTC)", max(cot_series.values(), key=lambda x: x.index[-1]))

    # --- Series semanales
    W = {t: semanal(s) for t, s in precios.items()}
    ref_w = W.get(REFERENCIA)

    # --- Mapa de rotación de motores
    motores = []
    if ref_w is not None:
        for t, nombre in MOTORES:
            if t not in W or W[t] is None:
                continue
            rs, X, Y = rrg(W[t], ref_w)
            if len(Y) < 12:
                continue
            XY = pd.concat([X, Y], axis=1).dropna()
            X, Y = XY.iloc[:, 0], XY.iloc[:, 1]
            f, extra = fase_motor(W[t].loc[:rs.index[-1]], rs, X, Y)
            fechas = list(X.index[-24:])
            r_m = r_ref = None
            try:
                r_m = (W[t].loc[fechas[-1]] / W[t].loc[fechas[0]] - 1) * 100
                r_ref = (ref_w.loc[fechas[-1]] / ref_w.loc[fechas[0]] - 1) * 100
            except Exception:  # noqa
                pass
            # Rentabilidades a 4, 13 y 26 semanas (motor y letras), hasta la última semana cerrada
            rents = {}
            pm_, pr_ = W[t].loc[:fechas[-1]], ref_w.loc[:fechas[-1]]
            for k in (4, 13, 26):
                if len(pm_) > k and len(pr_) > k:
                    rents[k] = ((pm_.iloc[-1] / pm_.iloc[-1 - k] - 1) * 100, (pr_.iloc[-1] / pr_.iloc[-1 - k] - 1) * 100)
            motores.append({"t": t, "nombre": nombre, "X": X.iloc[-1], "Y": Y.iloc[-1],
                            "xs": list(X.iloc[-24:].values), "ys": list(Y.iloc[-24:].values),
                            "fechas": fechas, "r_m": r_m, "r_ref": r_ref, "rents": rents,
                            "dy4": float(Y.iloc[-1] - Y.iloc[-5]),
                            "fase": f, "rsi": extra["rsi"], "alc": extra["alc"], "baj": extra["baj"],
                            "s_alc": extra["s_alc"], "s_baj": extra["s_baj"],
                            "fecha": X.index[-1]})

    # --- Variables
    baa_w = semanal(fr.get("BAA10Y"))
    vix_w = semanal(cb.get("VIX"))
    vix3_w = semanal(cb.get("VIX3M"))
    ratio_vix_w = (vix_w / vix3_w).dropna() if (vix_w is not None and vix3_w is not None) else None
    acwi_w = W.get("ACWI")

    ecy = cape = fecha_ecy = None
    if sh is not None:
        ss = sh.dropna(subset=["ECY"])
        if len(ss):
            ecy = float(ss["ECY"].iloc[-1])
            cape = float(ss["CAPE"].iloc[-1])
            fecha_ecy = ss.index[-1]
    comun = puerta_comun(baa_w, ratio_vix_w)
    conds = []
    for t, nom, eeuu in INDICES_COND:
        c = condicion_indice(W.get(t), ref_w, comun, ecy, eeuu)
        if c:
            c.update({"t": t, "nombre": nom})
            conds.append(c)
    if ecy is None:
        val_txt = "sin dato"
    elif ecy < 1.5:
        val_txt = "MUY CARA"
    elif ecy < 3:
        val_txt = "CARA"
    elif ecy < 4.5:
        val_txt = "RAZONABLE"
    else:
        val_txt = "BARATA"

    # TOP 3 (sin coberturas ni estrategias: volatilidad, managed futures, anti-beta)
    cand_top = [m for m in motores if GRUPO.get(m["t"]) not in EXCLUIR_TOP]
    top_salir = sorted([m for m in cand_top if m["fase"] in ("GIRO", "MADUREZ")],
                       key=lambda z: (0 if z["fase"] == "GIRO" else 1, z["dy4"]))[:3]
    top_ampliar = sorted([m for m in cand_top if m["fase"] == "ACELERACIÓN" and m["alc"] <= 1],
                         key=lambda z: -z["Y"])[:3]
    top_construir = sorted([m for m in cand_top if m["fase"] == "ARRANQUE"], key=lambda z: -z["Y"])[:3]

    # Vista en base 100 (cierres semanales normalizados; nunca precios)
    b100 = None
    if ref_w is not None and motores:
        idx = ref_w.index[-(B100_SEMANAS + 1):]
        ser = {}
        for t in [m["t"] for m in motores] + [REFERENCIA]:
            s = W.get(t)
            if s is None:
                continue
            s = s.reindex(idx)
            v = s.dropna()
            if v.empty:
                continue
            s = (s / v.iloc[0] * 100).round(2)
            ser[t] = [None if pd.isna(a) else float(a) for a in s.values]
        b100 = {"f": [f"{d:%Y-%m-%d}" for d in idx], "s": ser, "ref": REFERENCIA,
                "c": {m["t"]: COLOR_GRUPO.get(GRUPO.get(m["t"], ""), "#555") for m in motores},
                "n": {**{m["t"]: m["nombre"] for m in motores}, REFERENCIA: "Letras del Tesoro"}}

    # Liquidez neta de la Fed (millones de $)
    liq_w = None
    if all(k in fr for k in ("WALCL", "WTREGEN", "RRPONTSYD")):
        a = semanal(fr["WALCL"])
        b = semanal(fr["WTREGEN"])
        c = semanal(fr["RRPONTSYD"]) * 1000
        liq_w = pd.concat([a, b, c], axis=1).ffill().dropna()
        liq_w = (liq_w.iloc[:, 0] - liq_w.iloc[:, 1] - liq_w.iloc[:, 2]) / 1e6  # billones de $

    macro = []

    def add(v):
        if v is not None:
            macro.append(v)

    if "CPIAUCSL" in fr:
        cpi_m = mensual(fr["CPIAUCSL"])
        add(variable_macro("Inflación (IPC interanual, %)", (cpi_m / cpi_m.shift(12) - 1).dropna() * 100, "m", 6))
    enc = [mensual(fr[k]) for k in ("GACDFSA066MSFRBPHI", "GACDISA066MSFRBNY") if k in fr]
    if enc:
        crec = pd.concat(enc, axis=1).dropna().mean(axis=1).rolling(3).mean().dropna()
        add(variable_macro("Crecimiento (encuestas Fed, media 3 meses)", crec, "m", 6))
    if baa_w is not None:
        add(variable_macro("Estrés de crédito: diferencial Baa − Tesoro 10 años (puntos)", baa_w, "s", 13))
    if liq_w is not None:
        add(variable_macro("Liquidez neta de la Fed (billones $)", liq_w, "s", 13, "pct"))
    for sid, per in (("NFCI", 13), ("STLFSI4", 13), ("T10Y3M", 13), ("DFII10", 13), ("T10YIE", 13)):
        if sid in fr:
            add(variable_macro(FRED_SERIES[sid], semanal(fr[sid]), "s", per))
    if "DTWEXBGS" in fr:
        add(variable_macro("Dólar (índice amplio)", semanal(fr["DTWEXBGS"]), "s", 13, "pct"))
    if "ICSA" in fr:
        add(variable_macro("Peticiones de subsidio (media 4 semanas)", semanal(fr["ICSA"]).rolling(4).mean().dropna(), "s", 13, "pct"))
    if "SAHMREALTIME" in fr:
        add(variable_macro("Regla de Sahm", mensual(fr["SAHMREALTIME"]), "m", 6))
    if fci is not None:
        add(variable_macro("Condiciones financieras FCI-G (Fed)", mensual(fci), "m", 6))
    if "CPER" in W and "GLD" in W and W["CPER"] is not None and W["GLD"] is not None:
        cg = (W["CPER"] / W["GLD"]).dropna()
        add(variable_macro("Cobre / oro", cg, "s", 13, "pct"))
    if ratio_vix_w is not None:
        add(variable_macro("Estructura del VIX (VIX/VIX3M)", ratio_vix_w, "s", 4))
    for n, nom in (("DSPX", "Dispersión (DSPX)"), ("COR3M", "Correlación implícita (COR3M)")):
        if n in cb:
            add(variable_macro(nom, semanal(cb[n]), "s", 13))
    if "BAMLH0A0HYM2" in fr:
        add(variable_macro("Diferencial high yield (solo 3 años)", semanal(fr["BAMLH0A0HYM2"]), "s", 13))
    if "RSP" in W and "SPY" in W and W["RSP"] is not None and W["SPY"] is not None:
        add(variable_macro("Equiponderado / capitalización (RSP/SPY)", (W["RSP"] / W["SPY"]).dropna(), "s", 13, "pct"))
    mac = {v["nombre"]: v for v in macro}

    # --- Sentimiento (réplica Euphoriameter)
    sent = None
    comp = []
    if sh is not None:
        comp.append(pct(sh["CAPE"].dropna()))
    if vix_w is not None:
        comp.append(100 - pct(vix_w))
    if acwi_w is not None and len(acwi_w) > 210:
        dist = (acwi_w / acwi_w.rolling(200).mean() - 1).dropna()
        comp.append(pct(dist))
    if comp:
        sent = float(np.mean(comp))

    # --- Convicción
    votos = {}
    crec_v = mac.get("Crecimiento (encuestas Fed, media 3 meses)")
    sub_v = mac.get("Peticiones de subsidio (media 4 semanas)")
    sahm = fr["SAHMREALTIME"].iloc[-1] if "SAHMREALTIME" in fr else None
    if crec_v and sub_v:
        mejora_c = crec_v["impulso"] > 0
        mejora_s = sub_v["impulso"] < 0
        if (sahm is not None and sahm >= 0.5) or (not mejora_c and not mejora_s):
            votos["A · Régimen"] = -1
        elif mejora_c and mejora_s:
            votos["A · Régimen"] = 1
        else:
            votos["A · Régimen"] = 0
    if baa_w is not None and ratio_vix_w is not None and len(baa_w) > 14:
        sube = baa_w.iloc[-1] > baa_w.iloc[-14]
        rv = ratio_vix_w.iloc[-1]
        votos["B · Estrés"] = 1 if (not sube and rv < 1) else -1 if (sube and rv > 1) else 0
    if liq_w is not None and "NFCI" in fr:
        nf = semanal(fr["NFCI"])
        l_sube = liq_w.iloc[-1] > liq_w.iloc[-14]
        n_baja = nf.iloc[-1] < nf.iloc[-14]
        votos["C · Liquidez"] = 1 if (l_sube and n_baja) else -1 if (not l_sube and not n_baja) else 0
    if sent is not None:
        votos["E+F+G · Valoración, técnico y sentimiento"] = -1 if sent > 80 else 1 if sent < 20 else 0
    fase_acwi = next((m["fase"] for m in motores if m["t"] == "ACWI"), None)
    direccion = None
    if fase_acwi:
        direccion = 1 if fase_acwi in ("SUELO", "ARRANQUE", "ACELERACIÓN") else -1
    coinciden = sum(1 for v in votos.values() if direccion is not None and v == direccion)
    conv = "BAJA" if coinciden <= 1 else "MEDIA" if coinciden <= 3 else "ALTA"
    ritmo = {"BAJA": "No construir", "MEDIA": "Completar el tramo en 2–3 meses", "ALTA": "Completar el tramo en 1 mes"}[conv]
    d_txt = d_txt_html = None
    if "DSPX" in cb and "COR3M" in cb:
        p_d, p_c = pct(cb["DSPX"]), pct(cb["COR3M"])
        d_txt = ("Favorable a selección de valores (alfa)" if (p_d > 60 and p_c < 40)
                 else "Favorable a índice (beta)" if (p_d < 40 and p_c > 60) else "Neutral")
        d_txt_html = (f'{e(d_txt)} <span class="nota">· dispersión: percentil {fmt(p_d, 0)} (desde {fd(cb["DSPX"].index[0].year)}) · '
                      f'correlación: percentil {fmt(p_c, 0)} (desde {fd(cb["COR3M"].index[0].year)})</span>')

    # --- Calendario y estacionalidad
    eventos, en_blackout = calendario()
    est = estacionalidad(sh, precios.get("SPY"))

    # ------------------------------------------------------------------------
    # HTML
    # ------------------------------------------------------------------------
    H = []
    rrg_datos = {}
    H.append(CABECERA.replace("{{FECHA}}", f"{AHORA + pd.Timedelta(hours=1):%d-%m-%Y %H:%M}"))

    # ===== PESTAÑA 1: DIAGNOSTICADOR
    H.append('<section id="t1" class="pest activa">')

    # Condiciones de indexación
    H.append(f'<h2 class="primero">Condiciones de indexación · plazo de 6 a 12 meses{i("condiciones")}</h2>')
    H.append('<table><tr><th>Índice</th><th>Condiciones</th><th>Por qué</th><th>Tendencia</th><th>Fase</th>'
             f'<th>Distancia a media 200 sem.</th><th>Puerta{i("puntos")}</th></tr>')
    for c in conds:
        H.append(f'<tr><td><b>{e(c["nombre"])}</b> ({tk(c["t"])})</td>'
                 f'<td><span class="nivel" style="background:{COLOR_NIVEL[c["nivel"]]}">{c["nivel"]}</span></td>'
                 f'<td class="motivo">{e(c["motivo"])}</td><td>{c["tend"]}</td>'
                 f'<td>{e((c["fase"] or "—").capitalize())}</td><td>{fmt(c["dist"], 1, " %")}</td>'
                 f'<td>{c["puntos"]}/6 · tramo {c["tramo"]}</td></tr>')
    H.append('</table><p class="nota">Plazo: de 6 a 12 meses. Se calculan con cierres semanales: tendencia con la media de 40 semanas '
             '(unos 9 meses), fase del mapa con 26 semanas y puerta con la media de 200 semanas (unos 4 años). '
             'No sirven para decidir a días o semanas: para eso, la pestaña Opciones.</p>')

    # TOP 3
    def lista_top(ms):
        if not ms:
            return '<p class="nota">Ningún motor cumple ahora el criterio.</p>'
        return "<ol>" + "".join(f'<li>{tk(m["t"])} <span class="nota">{e(m["nombre"])}</span></li>' for m in ms) + "</ol>"
    H.append('<div class="tarjetas tops">')
    H.append(f'<div class="tarjeta top salir"><div class="t">Cerca de fin de ciclo · ir saliendo{i("top_salir")}</div>{lista_top(top_salir)}</div>')
    H.append(f'<div class="tarjeta top ampliar"><div class="t">Tendencia alcista con recorrido · ampliar{i("top_ampliar")}</div>{lista_top(top_ampliar)}</div>')
    H.append(f'<div class="tarjeta top construir"><div class="t">Podrían iniciar tendencia alcista · empezar a construir{i("top_construir")}</div>{lista_top(top_construir)}</div>')
    H.append('</div>')

    # Tarjetas de contexto
    H.append('<div class="tarjetas" style="margin-top:10px">')
    H.append(f'<div class="tarjeta"><div class="t">Valoración EE. UU. (ECY){i("valoracion")}</div><div class="v">{val_txt}</div>'
             f'<div class="s">ECY {fmt(ecy, 2, " %")} · CAPE {fmt(cape, 1)}'
             f'{" · " + fd(format(fecha_ecy, "%m-%Y")) if fecha_ecy is not None else ""}</div></div>')
    vt = "ACTIVO" if comun["veto"] else "no"
    H.append(f'<div class="tarjeta {"alerta" if comun["veto"] else ""}"><div class="t">Veto por crédito{i("veto")}</div><div class="v">{vt}</div>'
             f'<div class="s">Diferencial Baa − Tesoro 10 años: {fmt(baa_w.iloc[-1] if baa_w is not None else None, 2)} puntos · '
             f'{_con_signo(comun.get("baa_13s"))} en 13 semanas'
             f'{"" if comun.get("baa_13s") is None else (" (se estrecha: menos miedo)" if comun["baa_13s"] < 0 else " (se ensancha: más miedo)")}'
             f'</div></div>')
    H.append(f'<div class="tarjeta"><div class="t">Convicción{i("conviccion")}</div><div class="v">{conv}</div>'
             f'<div class="s">{e(ritmo)}</div></div>')
    H.append(f'<div class="tarjeta"><div class="t">Sentimiento (0–100){i("sentimiento")}</div><div class="v">{fmt(sent, 0)}</div>'
             f'<div class="s">0 = miedo · 100 = euforia</div></div>')
    H.append('</div>')

    # Puerta por índice
    def si_no(v, dato=""):
        if v is None:
            return "<td>sin dato</td>"
        return f'<td class="{"si" if v else ""}">{"SÍ" if v else "no"}{(" · " + dato) if dato else ""}</td>'
    rv = comun.get("ratio_vix")
    rv_txt = "" if rv is None else f'VIX/VIX3M {fmt(rv, 2)}: {"contango, lo normal" if rv < 1 else "invertida, miedo agudo"}'
    H.append(f'<h2>Puerta de indexación{i("puntos")}</h2>')
    H.append(f'<table><tr><th>Índice</th><th>Cerca de la media de 200 sem. (2){i("c_ma200")}</th>'
             f'<th>Crédito girando (2){i("c_credito")}</th><th>VIX vuelve a contango (1){i("c_vix")}</th>'
             f'<th>RSI semanal en sobreventa (1){i("c_rsi")}</th><th>Puntos</th><th>Tramo</th></tr>')
    for c in conds:
        pu = c["pu"]
        H.append(f'<tr><td>{e(c["nombre"])}</td>{si_no(pu["ma200"], "a " + fmt(pu["dist"], 1, " %"))}'
                 f'{si_no(comun["credito"])}{si_no(comun["vix"])}{si_no(pu["rsi"], "RSI " + fmt(pu["rsi_v"], 0))}'
                 f'<td><b>{c["puntos"]}/6</b></td><td>{c["tramo"]} de 3</td></tr>')
    H.append(f'</table><p class="nota">Crédito y VIX son comunes a todos los índices. {e(rv_txt)}. '
             'Cada condición cuenta si se cumplió en las últimas 13 semanas. 2, 4 y 6 puntos abren los tramos 1, 2 y 3.</p>')

    H.append(f'<h2>Grado de convicción{i("conviccion")}</h2><table><tr><th>Bloque</th><th>Voto</th></tr>')
    for k, v in votos.items():
        H.append(f'<tr><td>{e(k)}{i(CLAVE_BLOQUE.get(k, ""))}</td><td>{"a favor del riesgo" if v > 0 else "en contra del riesgo" if v < 0 else "neutral"}</td></tr>')
    H.append(f'<tr><td>Dirección del mapa (RV global){i("direccion")}</td><td>{e(fase_acwi or "sin dato")}</td></tr>')
    H.append(f'<tr><td>D · Beta o alfa{i("b_D")}</td><td>{d_txt_html if d_txt else "sin dato"}</td></tr>')
    H.append(f'<tr><td>Sentimiento (réplica Euphoriameter, 0–100){i("sentimiento")}</td><td>{fmt(sent, 0)}</td></tr>')
    H.append('</table>')

    # Mapa motores
    H.append(f'<h2>Mapa de rotación de motores{i("mapa")}</h2>')
    H.append('<div class="vista">Vista: <label><input type="radio" name="vista-motores" value="mapa" checked> Mapa de rotación</label>'
             f'<label><input type="radio" name="vista-motores" value="b100"> Evolución en base 100</label>{i("base100")}'
             '<label class="periodo" hidden>Periodo <select id="b100-per"><option value="24">24 semanas</option>'
             '<option value="52">1 año</option><option value="156">3 años</option><option value="260">5 años</option></select></label></div>')
    H.append(f'<p class="nota solo-mapa">Eje horizontal: fuerza relativa{i("fuerza")} · eje vertical: impulso{i("impulso")}. '
             'Rastro de 24 semanas con cierres semanales: □ inicio · ● ahora. La escala se ajusta a los motores marcados '
             '(por defecto, solo SPY). <b>Pulsa un motor para leer qué dice del mercado.</b></p>')
    H.append('<p class="nota solo-b100" hidden>Cuánto vale hoy lo que valía 100 al inicio del periodo (dividendos incluidos, en dólares). '
             'Línea negra discontinua: letras del Tesoro (BIL). Escala logarítmica. <b>Pulsa una línea para leer la dinámica del motor.</b></p>')
    grupos_presentes = [g for g in COLOR_GRUPO if any(GRUPO.get(m["t"]) == g for m in motores)]
    H.append('<div class="leyenda">' + "".join(
        f'<label><input type="checkbox" class="fg" value="{e(g)}"> <span class="chip" style="background:{COLOR_GRUPO[g]}"></span>{e(g)}</label>'
        for g in grupos_presentes) + '<label class="solo-mapa"><input type="checkbox" class="rastros" data-mapa="mapa-motores" checked> <b>Mostrar rastros</b></label></div>')
    H.append('<div class="filtros">' + "".join(
        f'<label data-tip="{e(m["t"] + " · " + DESC.get(m["t"], ""))}"><input type="checkbox" class="fm" value="{m["t"]}" data-g="{e(GRUPO.get(m["t"], ""))}"{" checked" if m["t"] == MOTOR_INICIAL else ""}> {e(m["t"])}</label>'
        for m in motores) + '</div>')
    rrg_datos["mapa-motores"] = {
        "c": 100, "q": CUAD_MOTOR, "tx": "Fuerza frente a la liquidez (100 = su media de 6 meses)",
        "ty": "Impulso (100 = sin cambio en 4 semanas)",
        "s": [{"k": m["t"], "g": GRUPO.get(m["t"], ""), "c": COLOR_GRUPO.get(GRUPO.get(m["t"], ""), "#555"),
               "tip": m["t"] + " · " + DESC.get(m["t"], "") + " Fase: " + m["fase"].lower() + ". Pulsa para ver su dinámica.",
               "x": [round(float(v), 2) for v in m["xs"]], "y": [round(float(v), 2) for v in m["ys"]]} for m in motores]}
    H.append('<div id="mapa-motores" class="solo-mapa" data-exp="mapa-motores"></div>')
    H.append('<div id="b100" class="solo-b100" data-exp="mapa-motores" hidden><div class="b100-graf"></div>'
             '<div class="b100-rank"></div></div>')
    exp_motores = {m["t"]: narrativa_motor(m) for m in motores}
    exp_motores = {k: v for k, v in exp_motores.items() if v}
    H.append('<label class="exp-elige">Ver la dinámica de <select class="exp-sel" data-mapa="mapa-motores"><option value="">— elige un motor —</option>'
             + "".join(f'<option value="{e(m["t"])}">{e(m["t"])} · {e(m["nombre"])}</option>' for m in motores if m["t"] in exp_motores)
             + '</select></label>')
    H.append('<div id="exp-mapa-motores" class="explica" data-mapa="mapa-motores" hidden></div>')
    H.append(f'<table class="ord"><tr><th>Motor</th><th>Nombre</th><th>Grupo</th><th>Fase{i("fases")}</th><th>Qué hacer</th>'
             f'<th>Fuerza{i("fuerza")}</th><th>Impulso{i("impulso")}</th><th>RSI sem.</th><th>Agotamiento{i("agotamiento")}</th></tr>')
    orden = ["ARRANQUE", "ACELERACIÓN", "MADUREZ", "GIRO", "SUELO", "REZAGADO"]

    def agot_txt(m):
        partes = []
        if m["alc"]:
            partes.append(f'alcista {m["alc"]} de 4')
        if m["baj"]:
            partes.append(f'bajista {m["baj"]} de 4')
        return " · ".join(partes) or "—"
    for m in sorted(motores, key=lambda z: orden.index(z["fase"])):
        g = GRUPO.get(m["t"], "")
        H.append(f'<tr><td><b>{tk(m["t"])}</b></td><td>{e(m["nombre"])}</td>'
                 f'<td><span class="chip" style="background:{COLOR_GRUPO.get(g, "#555")}"></span>{e(g)}</td>'
                 f'<td><span class="fase" style="background:{COLOR_FASE[m["fase"]]}">{m["fase"]}</span></td>'
                 f'<td>{"Solo referencia" if g in EXCLUIR_TOP else ACCION[m["fase"]]}</td><td>{fmt(m["X"], 1)}</td><td>{fmt(m["Y"], 1)}</td>'
                 f'<td>{fmt(m["rsi"], 0)}</td><td>{agot_txt(m)}</td></tr>')
    H.append('</table>')
    if motores:
        H.append(f'<p class="nota">Semana cerrada: {fd(format(motores[0]["fecha"], "%d-%m-%Y"))}. Proxies en dólares (el euro se ve con FXE). '
                 'Stock picking: ver bloque D. Market neutral: sin proxy. Volatilidad, managed futures y anti-beta: solo referencia (no entran en el TOP 3).</p>')

    # Mapa macro
    clave = ["Inflación (IPC interanual, %)", "Crecimiento (encuestas Fed, media 3 meses)",
             "Estrés de crédito: diferencial Baa − Tesoro 10 años (puntos)", "Liquidez neta de la Fed (billones $)"]
    cortos = {clave[0]: "Inflación", clave[1]: "Crecimiento", clave[2]: "Crédito (estrés)", clave[3]: "Liquidez"}
    color_macro = {"Inflación": "#c0392b", "Crecimiento": "#2f6fdb", "Crédito (estrés)": "#a0672a", "Liquidez": "#1f9d55"}
    reg = regimen_macro(mac, clave)
    rrg_datos["mapa-macro"] = {
        "c": 50, "rango": [-4, 104, -4, 104], "q": CUAD_MACRO,
        "tx": "Nivel: percentil sobre toda su historia (0 = mínimo, 100 = máximo)",
        "ty": "Impulso: percentil de su cambio reciente sobre toda su historia",
        "s": [{"k": cortos[k], "g": cortos[k], "c": color_macro[cortos[k]],
               "tip": cortos[k] + " · fase " + mac[k]["fase"].lower() + ". Pulsa para ver qué significa.",
               "x": [round(float(v), 1) for v in mac[k]["rx"]], "y": [round(float(v), 1) for v in mac[k]["ry"]]}
              for k in clave if k in mac and len(mac[k]["rx"]) >= 1]}
    if reg:
        H.append(f'<h2>Régimen macro{i("regimen")}</h2>')
        H.append(f'<div class="tarjeta regimen"><div class="v">{reg["nombre"]}</div>'
                 f'<div class="s">{e(reg["desc"])} · inflación {fmt(reg["inf"], 1)} % ({fd(format(reg["f_inf"], "%m-%Y"))}) · '
                 f'encuestas de actividad {fmt(reg["cre"], 1)} ({fd(format(reg["f_cre"], "%m-%Y"))})</div>'
                 f'<p>{e(reg["lectura"])}</p><p class="nota">{e(reg["favor"])}</p>'
                 + (f'<p class="nota">Inflación por encima del {fmt(INFLACION_ALTA, 0)} %: el mercado tiende a leer los datos fuertes de '
                    'actividad como «más tipos» (buenas noticias = malas) y los flojos como «bajadas de tipos» (malas noticias = buenas), '
                    'mientras no haya recesión.</p>' if reg["inf_alta"] else "")
                 + '</div>')
    H.append(f'<h2>Mapa macro{i("mapa_macro")}</h2><p class="nota">Eje horizontal: nivel{i("nivel_macro")} · eje vertical: impulso{i("impulso_macro")}. '
             'Los dos ejes, de 0 a 100, son percentiles sobre <b>toda la historia</b> de cada variable; el rastro solo enseña el camino '
             'de las últimas 24 semanas (□ inicio · ● ahora; los datos mensuales tienen un punto por mes). '
             '<b>Pulsa una variable para leer qué significa para el mercado.</b></p>')
    H.append('<div class="leyenda">' + "".join(
        f'<span><span class="chip" style="background:{c}"></span>{e(g)}</span>' for g, c in color_macro.items()) + '</div>')
    H.append('<div id="mapa-macro" data-exp="mapa-macro"></div>')
    exp_macro = {cortos[k]: narrativa_macro(cortos[k], mac[k], color_macro[cortos[k]], reg) for k in clave if k in mac}
    exp_macro = {k: v for k, v in exp_macro.items() if v}
    H.append('<label class="exp-elige">Ver la dinámica de <select class="exp-sel" data-mapa="mapa-macro"><option value="">— elige una variable —</option>'
             + "".join(f'<option value="{e(k)}">{e(k)}</option>' for k in exp_macro) + '</select></label>')
    H.append('<div id="exp-mapa-macro" class="explica" data-mapa="mapa-macro" hidden></div>')

    H.append(f'<h2>Variables{i("variables")}</h2><table class="ord"><tr><th>Variable</th><th>Valor</th><th>Fecha</th><th>Percentil histórico{i("nivel_macro")}</th><th>Cambio reciente{i("impulso_macro")}</th><th>Fase</th></tr>')
    for v in macro:
        f_ref = v["fecha"] + pd.offsets.MonthEnd(0) if v["frecuencia"] == "m" else v["fecha"]
        viejo = not antiguedad_ok(f_ref, "m" if v["frecuencia"] == "m" else "s")
        unidad = " %" if v["modo"] == "pct" else ""
        per = f'{v["periodos"]} {"meses" if v["frecuencia"] == "m" else "sem."}'
        H.append(f'<tr><td>{e(v["nombre"])}{i(v["nombre"])}</td><td>{fmt(v["valor"], 0 if abs(v["valor"]) >= 1000 else 2)}</td>'
                 f'<td class="{"viejo" if viejo else ""}">{fd(format(v["fecha"], "%d-%m-%Y"))}{" ⚠" if viejo else ""}</td>'
                 f'<td>{fmt(v["nivel"], 0)} <span class="nota">desde {fd(v["desde"])}</span></td><td>{fmt(v["impulso"], 2, unidad)} ({per})</td>'
                 f'<td><span class="fase" style="background:{COLOR_FASE[v["fase"]]}">{v["fase"]}</span></td></tr>')
    H.append('</table><p class="nota">⚠ = dato desfasado.</p>')
    H.append('</section>')

    # ===== PESTAÑA 2: OPCIONES
    H.append('<section id="t2" class="pest">')
    H.append('<p class="nota">Señales de días a semanas: sirven para elegir el momento de cada tramo, no para el régimen.</p>')
    H.append('<div class="tarjetas">')
    if gm:
        signo = "POSITIVA" if gm["gex_mm"] > 0 else "NEGATIVA"
        efecto = "amortigua los movimientos" if gm["gex_mm"] > 0 else "amplifica los movimientos"
        hg = gm["hist"]["gex_mm"].dropna()
        inten = (f"intensidad: percentil {fmt(pct(hg), 0)} de su histórico propio (desde {fd(format(hg.index[0], '%m-%Y'))})"
                 if len(hg) >= 20 else "intensidad: histórico aún corto")
        H.append(f'<div class="tarjeta {"alerta" if gm["gex_mm"] < 0 else ""}"><div class="t">Gamma de los creadores de mercado (aprox.){i("gamma")}</div>'
                 f'<div class="v">{signo}</div><div class="s">{efecto} · {inten}</div></div>')
        if gm["giro"]:
            dist = (gm["spot"] / gm["giro"] - 1) * 100
            H.append(f'<div class="tarjeta"><div class="t">Nivel de giro de la gamma{i("giro")}</div><div class="v">{fmt(gm["giro"], 0)}</div>'
                     f'<div class="s">S&P 500 a {fmt(dist, 1, " %")} del giro</div></div>')
        H.append(f'<div class="tarjeta"><div class="t">Muros (próximos 70 días){i("muros")}</div><div class="v">{fmt(gm["muro_put"], 0)} – {fmt(gm["muro_call"], 0)}</div>'
                 f'<div class="s">soporte put · resistencia call</div></div>')
    else:
        H.append('<div class="tarjeta"><div class="t">Gamma</div><div class="v">sin dato</div></div>')
    if "SKEW" in cb:
        sk = cb["SKEW"]
        sk1 = sk[sk.index >= sk.index[-1] - pd.Timedelta(days=365)]
        H.append(f'<div class="tarjeta"><div class="t">SKEW{i("skew")}</div><div class="v">{fmt(sk.iloc[-1], 0)}</div>'
                 f'<div class="s">percentil {fmt(pct(sk), 0)} histórico (desde {fd(sk.index[0].year)}) · {fmt(pct(sk1), 0)} último año · '
                 f'{fd(format(sk.index[-1], "%d-%m-%Y"))}</div></div>')
    H.append('</div>')

    if pc is not None and len(pc):
        H.append(f'<h2>Ratio put/call{i("putcall")}</h2><table><tr><th>Tipo</th><th>Último</th><th>Media 10 días</th><th>Percentil (media 10 d.) sobre su histórico propio</th></tr>')
        nombres_pc = {"total": "Total", "acciones": "Acciones", "indices": "Índices", "spx": "S&P 500"}
        for col, nom in nombres_pc.items():
            if col not in pc.columns:
                continue
            s = pc[col].dropna()
            if not len(s):
                continue
            m10 = s.rolling(10).mean().dropna()
            pp = fmt(pct(m10), 0) if len(m10) >= 60 else "historia corta"
            H.append(f'<tr><td>{nom}</td><td>{fmt(s.iloc[-1], 2)}</td><td>{fmt(m10.iloc[-1] if len(m10) else None, 2)}</td><td>{pp}</td></tr>')
        H.append(f'</table><p class="nota">Último dato: {fd(format(pc.index[-1], "%d-%m-%Y"))}. Percentil alto = mucha cobertura (miedo); '
                 f'bajo = complacencia. Historia desde {fd(format(pc.index[0], "%m-%Y"))}.</p>')
        if "acciones" in pc.columns:
            H.append(svg_linea(pc["acciones"].rolling(10).mean().dropna().iloc[-250:]))
            H.append('<p class="nota">Put/call de acciones, media de 10 días, último año.</p>')
    if gm and len(gm["hist"]) > 5:
        H.append(svg_linea(gm["hist"]["gex_mm"].dropna(), ref=0, color="#7a3fd1"))
        H.append('<p class="nota">Gamma neta, histórico propio.</p>')

    if cot_series:
        H.append(f'<h2>Posicionamiento (CFTC, semanal){i("cot")}</h2><table><tr><th>Mercado · grupo</th><th>Neto (% del interés abierto)</th>'
                 '<th>Cambio 4 semanas</th><th>Percentil 3 años</th><th>Fecha</th></tr>')
        for k, s in cot_series.items():
            s3 = s[s.index >= s.index[-1] - pd.Timedelta(days=3 * 365)]
            ch = s.iloc[-1] - s.iloc[-5] if len(s) > 5 else np.nan
            p3 = pct(s3)
            cls = "extremo" if (p3 >= 90 or p3 <= 10) else ""
            H.append(f'<tr><td>{e(k)}</td><td>{fmt(s.iloc[-1], 1, " %")}</td><td>{fmt(ch, 1)}</td>'
                     f'<td class="{cls}">{fmt(p3, 0)}</td><td>{fd(format(s.index[-1], "%d-%m-%Y"))}</td></tr>')
        H.append('</table><p class="nota">Percentil de los últimos 3 años ≥ 90 o ≤ 10 = posicionamiento extremo (resaltado).</p>')
    H.append('</section>')

    # ===== PESTAÑA 3: CALENDARIO
    H.append('<section id="t3" class="pest">')
    H.append(f'<div class="tarjetas"><div class="tarjeta {"alerta" if en_blackout else ""}"><div class="t">Blackout de recompras (estimado){i("blackout")}</div>'
             f'<div class="v">{"SÍ" if en_blackout else "NO"}</div><div class="s">estimación, no dato oficial</div></div>')
    if "anio_actual" in est:
        H.append(f'<div class="tarjeta"><div class="t">Ciclo presidencial{i("ciclo")}</div><div class="v" style="font-size:1.1em">{e(est["anio_actual"])}</div></div>')
    H.append('</div>')
    H.append('<h2>Próximos 120 días</h2><table><tr><th>Fecha</th><th>Evento</th><th>Tipo</th></tr>')
    dias_sem = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
    for d, t, c in eventos:
        H.append(f'<tr><td>{fd(dias_sem[d.weekday()] + " " + format(d, "%d-%m-%Y"))}</td><td>{con_fechas(t)}</td>'
                 f'<td><span class="tipo">{e(c)}</span>{i(c)}</td></tr>')
    H.append('</table>')
    if "meses" in est:
        nm = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
        H.append(f'<h2>Estacionalidad del S&amp;P 500 (desde {fd(est["desde"])}){i("estacionalidad")}</h2>'
                 '<table><tr><th>Mes</th><th>Media</th><th>Mediana</th><th>% meses positivos</th></tr>')
        for mes, fila in est["meses"].iterrows():
            marca = ' class="hoy"' if mes in (HOY.month, HOY.month % 12 + 1) else ""
            H.append(f'<tr{marca}><td>{nm[mes - 1]}</td><td>{fmt(fila["media"], 2, " %")}</td>'
                     f'<td>{fmt(fila["mediana"], 2, " %")}</td><td>{fmt(fila["positivos"], 0, " %")}</td></tr>')
        H.append('</table>')
        H.append(f'<h2>Ciclo presidencial (rentabilidad anual){i("ciclo")}</h2><table><tr><th>Año del ciclo</th><th>Media</th><th>Mediana</th><th>% años positivos</th></tr>')
        for nom, me, md, po in est["ciclo"]:
            marca = ' class="hoy"' if nom == est["anio_actual"] else ""
            H.append(f'<tr{marca}><td>{nom}</td><td>{fmt(me, 1, " %")}</td><td>{fmt(md, 1, " %")}</td><td>{fmt(po, 0, " %")}</td></tr>')
        H.append('</table>')
    if "cambio_mes" in est:
        a, b, desde = est["cambio_mes"]
        H.append(f'<p class="nota">Cambio de mes (último día + 3 primeros, desde {fd(desde)}): media diaria {fmt(a, 3, " %")} frente a {fmt(b, 3, " %")} el resto de días.{i("cambio_mes")}</p>')
    H.append('<p class="nota">Evidencia académica de la estacionalidad: débil e inestable.</p>')
    H.append('</section>')

    # ===== Estado de las fuentes
    H.append('<section class="fuentes"><h2>Estado de los datos</h2>')
    if ERRORES:
        H.append('<p class="error"><b>Fuentes con fallo hoy:</b> ' + "; ".join(e(n) for n, _ in ERRORES) + '</p>')
    else:
        H.append('<p>Todas las fuentes se han descargado correctamente.</p>')
    H.append('<details><summary>Última fecha de cada fuente</summary><table>')
    for k in sorted(FECHAS):
        H.append(f'<tr><td>{e(k)}</td><td>{fd(format(pd.Timestamp(FECHAS[k]), "%d-%m-%Y"))}</td></tr>')
    H.append('</table></details></section>')

    # ===== Resumen en texto (para lectura automática)
    R = [f"RESUMEN {HOY:%d-%m-%Y}",
         "Condiciones: " + "; ".join(f"{c['nombre']}={c['nivel']} (puerta {c['puntos']}/6, tramo {c['tramo']})" for c in conds)
         + f"; valoración EE. UU.={val_txt} (ECY {fmt(ecy, 2)} %); veto={'sí' if comun['veto'] else 'no'}",
         "Top salir: " + ", ".join(m["t"] for m in top_salir) + " · ampliar: " + ", ".join(m["t"] for m in top_ampliar)
         + " · construir: " + ", ".join(m["t"] for m in top_construir),
         f"Convicción={conv}; votos=" + ", ".join(f"{k}:{v:+d}" for k, v in votos.items()) + f"; dirección RV global={fase_acwi}; beta/alfa={d_txt}; sentimiento={fmt(sent, 0)}",
         "Motores: " + "; ".join(f"{m['t']}={m['fase']}" for m in motores),
         "Macro: " + "; ".join(f"{v['nombre']}={v['fase']} (p{fmt(v['nivel'], 0)})" for v in macro)]
    if gm:
        R.append(f"Gamma={'positiva' if gm['gex_mm'] > 0 else 'negativa'}, giro={fmt(gm['giro'], 0)}, S&P={fmt(gm['spot'], 0)}")
    R.append(f"Blackout estimado={'sí' if en_blackout else 'no'}")
    if ERRORES:
        R.append("Fallos: " + ", ".join(n for n, _ in ERRORES))
    H.append('<pre class="resumen">' + con_fechas("\n".join(R)) + '</pre>')

    H.append('<div id="modal" class="modal"><div class="caja"><button class="cerrar" aria-label="Cerrar">×</button>'
             '<h3 id="modal-t"></h3><div id="modal-c"></div></div></div>')
    H.append('<script id="info-datos" type="application/json">' + info_json() + '</script>')
    H.append('<script id="rrg-datos" type="application/json">'
             + json.dumps(rrg_datos, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/") + '</script>')
    H.append('<script id="exp-datos" type="application/json">'
             + json.dumps({"mapa-motores": exp_motores, "mapa-macro": exp_macro}, ensure_ascii=False).replace("</", "<\\/")
             + '</script>')
    H.append('<script id="b100-datos" type="application/json">'
             + json.dumps(b100, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/") + '</script>')
    H.append(PIE)
    with open(os.path.join(CARPETA_WEB, "index.html"), "w", encoding="utf-8") as f:
        f.write("".join(H))
    print("\n".join(R))
    for n, m in ERRORES:
        print("FALLO", n, m)


CABECERA = """<!DOCTYPE html>
<html lang="es" class="tema-azul"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Diagnosticador de mercados</title>
<style>
html.tema-azul{--pagina:linear-gradient(170deg,#eef5fc 0%,#dce9f6 55%,#cfe0f2 100%);--panel:rgba(255,255,255,.72);--th:rgba(225,236,248,.95);--b:#c9d8e8;--f:#1b2a3a;--s:#56677a;--acento:#2f6fdb;--fd:#7a4fa8}
html.tema-gris{--pagina:linear-gradient(170deg,#f4f5f7 0%,#e6e9ee 100%);--panel:rgba(255,255,255,.8);--th:#eceff3;--b:#d8dde3;--f:#1d2733;--s:#5b6776;--acento:#4a5d75;--fd:#7a4fa8}
html.tema-blanco{--pagina:#ffffff;--panel:#f6f8fa;--th:#f6f8fa;--b:#e3e7ec;--f:#1d2733;--s:#5b6776;--acento:#2f6fdb;--fd:#7a4fa8}
html.tema-arena{--pagina:linear-gradient(170deg,#fbf8f2 0%,#f1eadc 100%);--panel:rgba(255,255,255,.75);--th:#f3ecdf;--b:#e2d7c3;--f:#2b2620;--s:#6c6253;--acento:#a0672a;--fd:#8a4f7d}
*{box-sizing:border-box}
[hidden]{display:none!important}
.fd{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;font-size:.84em;color:var(--fd);font-style:normal;font-weight:400;white-space:nowrap;letter-spacing:-.02em}
td.viejo .fd{color:#c0392b}
html{background:var(--pagina);background-attachment:fixed;min-height:100%}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;color:var(--f);background:transparent;margin:0 auto;padding:14px;max-width:1100px;font-size:15px;line-height:1.4}
header{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:10px}
h1{font-size:1.25em;margin:0}
.fecha{color:var(--s);font-size:.85em}
.apariencia{font-size:.8em;color:var(--s)}
.apariencia select{font-size:1em;border:1px solid var(--b);border-radius:6px;background:var(--panel);color:var(--f);padding:2px 4px}
nav{display:flex;gap:6px;border-bottom:1px solid var(--b);margin-bottom:14px;flex-wrap:wrap}
nav button{border:0;background:none;padding:9px 14px;font-size:1em;cursor:pointer;color:var(--s);border-bottom:3px solid transparent}
nav button.activa{color:var(--f);border-bottom-color:var(--acento);font-weight:600}
.pest{display:none}.pest.activa{display:block}
h2{font-size:1.05em;margin:22px 0 8px}
.tarjetas{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px}
.tarjeta{background:var(--panel);border:1px solid var(--b);border-radius:10px;padding:10px 12px}
.tarjeta.alerta{background:#fff1ec;border-color:#f3b49b}
.tarjeta .t{font-size:.8em;color:var(--s)}.tarjeta .v{font-size:1.5em;font-weight:700;margin:2px 0}.tarjeta .s{font-size:.8em;color:var(--s)}
table{border-collapse:collapse;width:fit-content;max-width:100%;font-size:.88em;display:block;overflow-x:auto;background:var(--panel);border-radius:8px}
th,td{padding:6px 8px;border-bottom:1px solid var(--b);text-align:left;white-space:nowrap}
th{background:var(--th);font-weight:600;cursor:default}
table.ord th{cursor:pointer}
td.si{color:#1f7a45;font-weight:700}td.viejo{color:#c0392b}td.extremo{background:#fff1ec;font-weight:700}
tr.hoy td{background:rgba(47,111,219,.12);font-weight:600}
.fase{color:#fff;border-radius:6px;padding:2px 7px;font-size:.8em;font-weight:600}
.tipo{background:var(--th);border:1px solid var(--b);border-radius:6px;padding:1px 6px;font-size:.8em}
.nota{color:var(--s);font-size:.82em;margin:6px 0}
.grafico{width:100%;max-width:760px;height:auto;display:block;margin:6px 0;background:var(--panel);border-radius:10px}
.mini{width:100%;max-width:760px;height:auto;display:block;background:var(--panel);border-radius:8px;margin-top:8px}
.grafico .cuad{font-size:12px;font-weight:700;opacity:.7}.grafico .eje{font-size:12px;fill:#56677a}.grafico .etq{font-size:11px;font-weight:600;fill:#1b2a3a}
.filtros{display:flex;flex-wrap:wrap;gap:4px 10px;font-size:.8em;margin:4px 0}
.fuentes{margin-top:26px;border-top:1px solid var(--b);padding-top:6px;font-size:.88em}
.error{color:#c0392b}
th{white-space:normal;vertical-align:bottom;min-width:60px}
.grafico.resaltando g.serie{opacity:.12}.grafico.resaltando g.serie.activa{opacity:1}
.grafico g.serie.activa polyline{stroke-width:3;stroke-opacity:.95}.grafico g.serie.activa text{font-size:13px}
.sin-rastro .r{display:none}
.nivel{color:#fff;border-radius:6px;padding:3px 8px;font-size:.8em;font-weight:700;white-space:nowrap}
td.motivo{white-space:normal;min-width:220px;max-width:340px}
h2.primero{margin-top:6px}
.tops{margin-top:12px}.tops .tarjeta ol{margin:6px 0 0;padding-left:20px}.tops .tarjeta li{margin:2px 0}
.top.salir{border-left:4px solid #d9534f}.top.ampliar{border-left:4px solid #2e9d55}.top.construir{border-left:4px solid #2f6fdb}
.chip{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:5px;vertical-align:baseline}
.leyenda{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:.82em;margin:6px 0}
.tk{border-bottom:1px dotted currentColor;cursor:help}
#tip{position:fixed;display:none;z-index:60;max-width:300px;background:#1b2a3a;color:#fff;padding:7px 10px;border-radius:7px;font-size:12px;line-height:1.35;pointer-events:none;box-shadow:0 4px 14px rgba(0,0,0,.25)}
.info{border:0;background:none;color:var(--acento);cursor:pointer;font-size:.95em;padding:0 3px;line-height:1;vertical-align:baseline}
.modal{display:none;position:fixed;inset:0;background:rgba(20,30,45,.45);z-index:50;align-items:flex-start;justify-content:center;padding:36px 12px;overflow:auto}
.modal.abierto{display:flex}
.modal .caja{background:#fff;color:#1b2a3a;max-width:640px;width:100%;border-radius:12px;padding:16px 20px;box-shadow:0 10px 30px rgba(0,0,0,.2);position:relative;font-size:.93em;line-height:1.5}
.modal h3{margin:0 28px 8px 0;font-size:1.1em}.modal ul{padding-left:20px}
.modal .cerrar{position:absolute;top:8px;right:12px;border:0;background:none;font-size:1.5em;cursor:pointer;color:#56677a}
.fuentes-info{font-size:.85em;color:#56677a;border-top:1px solid #e3e7ec;padding-top:8px;margin-top:10px}
.fuentes-info a{color:#2f6fdb}
.resumen{white-space:pre-wrap;font-size:.75em;color:var(--s);background:var(--panel);padding:8px;border-radius:8px;margin-top:16px}
.vista{display:flex;flex-wrap:wrap;align-items:center;gap:4px 14px;font-size:.88em;margin:2px 0 6px}
.vista label{cursor:pointer;white-space:nowrap}
.vista select,.exp-elige select{font-size:1em;border:1px solid var(--b);border-radius:6px;background:var(--panel);color:var(--f);padding:2px 4px;max-width:100%}
.exp-elige{display:block;font-size:.85em;color:var(--s);margin:6px 0 2px}
.explica{background:var(--panel);border:1px solid var(--b);border-left:4px solid var(--acento);border-radius:10px;padding:10px 14px;margin:6px 0 14px;max-width:760px;position:relative;font-size:.9em;line-height:1.5}
.explica h3{margin:0 28px 6px 0;font-size:1.05em}
.explica p{margin:6px 0}
.explica .exp-ver{font-size:1.02em;background:rgba(47,111,219,.07);border-radius:8px;padding:6px 9px}
.explica .exp-aviso{color:#a0522d;font-weight:600}
.explica ul.exp-lista{margin:4px 0 6px;padding-left:18px}.explica ul.exp-lista li{margin:2px 0}
.explica table.exp-tabla{width:auto;margin:4px 0 8px;font-size:.92em;background:none}
.explica table.exp-tabla th,.explica table.exp-tabla td{padding:2px 14px 2px 0;text-align:right;border:0;background:none;font-variant-numeric:tabular-nums}
.explica table.exp-tabla th:first-child,.explica table.exp-tabla td:first-child{text-align:left}
.grafico .tk{font-size:10.5px;fill:#56677a}
.tarjeta.regimen{max-width:760px}.tarjeta.regimen p{margin:6px 0;font-size:.9em}
.explica .et{display:inline-block;color:var(--s);font-size:.78em;font-weight:700;text-transform:uppercase;letter-spacing:.04em;margin-right:5px}
.cerrar-exp{position:absolute;top:6px;right:10px;border:0;background:none;font-size:1.4em;cursor:pointer;color:var(--s);line-height:1}
[data-exp] svg.grafico{cursor:pointer}
.grafico g.serie>*:not(text){pointer-events:none}
.grafico.fijando g.serie{opacity:.15}.grafico.fijando g.serie.fija{opacity:1}
.grafico g.serie.fija polyline{stroke-width:3;stroke-opacity:.95}.grafico g.serie.fija text{font-size:13px}
.grafico g.serie.activa path.l,.grafico g.serie.fija path.l{stroke-width:3;stroke-opacity:1}
.grafico g.serie.ref{cursor:default}
.grafico .ytk{font-size:10.5px;fill:#56677a}
.grafico .fdt{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;font-size:10.5px;fill:var(--fd)}
.grafico .etq2{font-size:10.5px;font-weight:600}
.b100-rank ol{columns:3 210px;column-gap:18px;margin:4px 0 6px;padding-left:24px;font-size:.84em}
.b100-rank li{break-inside:avoid;margin:1px 0}
.b100-rank li.ref{font-weight:700}
.b100-rank .v{font-variant-numeric:tabular-nums;font-weight:600}
.b100-rank .mas{color:#1f7a45}.b100-rank .menos{color:#c0392b}
</style>
<script>try{var t=localStorage.getItem('tema');if(t){document.documentElement.className='tema-'+t;}}catch(e){}</script>
</head><body>
<header><h1>Diagnosticador de mercados</h1>
<span class="fecha">Actualizado <span class="fd">{{FECHA}}</span> (hora peninsular aprox.)</span>
<label class="apariencia">Apariencia
<select id="tema"><option value="azul">Azul suave</option><option value="gris">Gris claro</option><option value="arena">Arena</option><option value="blanco">Blanco</option></select></label>
</header>
<nav><button class="activa" data-t="t1">Diagnosticador</button><button data-t="t2">Opciones</button><button data-t="t3">Calendario</button></nav>
"""

PIE = r"""
<script>
(function(){var sel=document.getElementById('tema');var actual=document.documentElement.className.replace('tema-','');
 sel.value=actual;sel.addEventListener('change',function(){document.documentElement.className='tema-'+sel.value;
 try{localStorage.setItem('tema',sel.value);}catch(e){}});})();
(function(){var datos={};try{datos=JSON.parse(document.getElementById('info-datos').textContent);}catch(e){}
 var m=document.getElementById('modal');
 function cerrar(){m.classList.remove('abierto');}
 document.addEventListener('click',function(ev){var b=ev.target.closest&&ev.target.closest('.info');
  if(b){ev.preventDefault();ev.stopPropagation();var d=datos[b.getAttribute('data-k')];if(!d)return;
   document.getElementById('modal-t').textContent=d.t;document.getElementById('modal-c').innerHTML=d.h;m.classList.add('abierto');return;}
  if(ev.target===m||(ev.target.classList&&ev.target.classList.contains('cerrar')))cerrar();},true);
 document.addEventListener('keydown',function(ev){if(ev.key==='Escape')cerrar();});})();
(function(){var tip=document.createElement('div');tip.id='tip';document.body.appendChild(tip);
 document.addEventListener('mouseover',function(ev){var t=ev.target.closest&&ev.target.closest('[data-tip]');
  if(!t){tip.style.display='none';return;}tip.textContent=t.getAttribute('data-tip');tip.style.display='block';});
 document.addEventListener('mousemove',function(ev){if(tip.style.display!=='block')return;
  var x=ev.clientX+14,y=ev.clientY+16;if(x+310>window.innerWidth)x=Math.max(4,window.innerWidth-310);
  if(y+80>window.innerHeight)y=ev.clientY-60;tip.style.left=x+'px';tip.style.top=y+'px';});
 document.querySelectorAll('.fg').forEach(function(c){c.addEventListener('change',function(){
  document.querySelectorAll('.fm[data-g="'+c.value+'"]').forEach(function(m){m.checked=c.checked;
   m.dispatchEvent(new Event('change'));});});});})();
document.querySelectorAll('.rastros').forEach(function(c){c.addEventListener('change',function(){
 var m=document.getElementById(c.getAttribute('data-mapa'));if(m)m.classList.toggle('sin-rastro',!c.checked);});});
document.querySelectorAll('nav button').forEach(function(b){b.addEventListener('click',function(){
 document.querySelectorAll('nav button').forEach(function(x){x.classList.remove('activa')});
 document.querySelectorAll('.pest').forEach(function(x){x.classList.remove('activa')});
 b.classList.add('activa');document.getElementById(b.dataset.t).classList.add('activa');});});
// ---- Mapas de rotación (motores y macro), con escalas numéricas
(function(){var R={};try{R=JSON.parse(document.getElementById('rrg-datos').textContent)||{};}catch(e){}
 function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
 function num(v){return String(+v.toFixed(2)).replace('.',',');}
 function paso(m){var c=[0.5,1,2,2.5,5,10,20,25,50];for(var i=0;i<c.length;i++)if(m/c[i]<=4)return c[i];return 100;}
 function dibujar(id){var cont=document.getElementById(id),D=R[id];if(!cont||!D)return;
  var ser=D.s||[];
  if(id==='mapa-motores'){var vis={};document.querySelectorAll('.fm').forEach(function(c){if(c.checked)vis[c.value]=1;});
   ser=ser.filter(function(z){return vis[z.k];});}
  if(!ser.length){cont.innerHTML='<p class="nota">Marca algún motor en las casillas de arriba.</p>';return;}
  var c=D.c,x0,x1,y0,y1;
  if(D.rango){x0=D.rango[0];x1=D.rango[1];y0=D.rango[2];y1=D.rango[3];}
  else{var dx=2,dy=2;ser.forEach(function(z){z.x.forEach(function(v){dx=Math.max(dx,Math.abs(v-c));});
    z.y.forEach(function(v){dy=Math.max(dy,Math.abs(v-c));});});dx*=1.15;dy*=1.15;x0=c-dx;x1=c+dx;y0=c-dy;y1=c+dy;}
  var W=680,H=520,ml=56,mr=16,mt=16,mb=52;
  function X(v){return ml+(v-x0)/(x1-x0)*(W-ml-mr);}function Y(v){return H-mb-(v-y0)/(y1-y0)*(H-mt-mb);}
  var cx=X(c),cy=Y(c),o=['<svg viewBox="0 0 '+W+' '+H+'" class="grafico" role="img">'];
  o.push('<rect x="'+cx+'" y="'+mt+'" width="'+(W-mr-cx)+'" height="'+(cy-mt)+'" fill="#e8f6ee"/>');
  o.push('<rect x="'+cx+'" y="'+cy+'" width="'+(W-mr-cx)+'" height="'+(H-mb-cy)+'" fill="#fdf5dc"/>');
  o.push('<rect x="'+ml+'" y="'+cy+'" width="'+(cx-ml)+'" height="'+(H-mb-cy)+'" fill="#fbe9e7"/>');
  o.push('<rect x="'+ml+'" y="'+mt+'" width="'+(cx-ml)+'" height="'+(cy-mt)+'" fill="#e7effc"/>');
  var sx=D.rango?10:paso(x1-c),sy=D.rango?10:paso(y1-c),t;
  for(t=Math.ceil(x0/sx-1e-9)*sx;t<=x1+1e-9;t+=sx){if(D.rango&&(t<0||t>100))continue;var px=X(t).toFixed(1);
   o.push('<line x1="'+px+'" y1="'+mt+'" x2="'+px+'" y2="'+(H-mb)+'" stroke="#ffffff" stroke-width="1"/>');
   o.push('<line x1="'+px+'" y1="'+(H-mb)+'" x2="'+px+'" y2="'+(H-mb+4)+'" stroke="#8a97a8"/>');
   o.push('<text x="'+px+'" y="'+(H-mb+16)+'" text-anchor="middle" class="tk">'+num(t)+'</text>');}
  for(t=Math.ceil(y0/sy-1e-9)*sy;t<=y1+1e-9;t+=sy){if(D.rango&&(t<0||t>100))continue;var py=Y(t).toFixed(1);
   o.push('<line x1="'+ml+'" y1="'+py+'" x2="'+(W-mr)+'" y2="'+py+'" stroke="#ffffff" stroke-width="1"/>');
   o.push('<line x1="'+(ml-4)+'" y1="'+py+'" x2="'+ml+'" y2="'+py+'" stroke="#8a97a8"/>');
   o.push('<text x="'+(ml-7)+'" y="'+(+py+3.5)+'" text-anchor="end" class="tk">'+num(t)+'</text>');}
  o.push('<line x1="'+cx+'" y1="'+mt+'" x2="'+cx+'" y2="'+(H-mb)+'" stroke="#999"/>');
  o.push('<line x1="'+ml+'" y1="'+cy+'" x2="'+(W-mr)+'" y2="'+cy+'" stroke="#999"/>');
  var q=D.q;[[q[0],W-mr-6,mt+16,'end','#1f7a45'],[q[1],W-mr-6,H-mb-8,'end','#9a7400'],[q[2],ml+6,H-mb-8,'start','#a33'],
   [q[3],ml+6,mt+16,'start','#2457b0']].forEach(function(a){o.push('<text x="'+a[1]+'" y="'+a[2]+'" text-anchor="'+a[3]+'" class="cuad" fill="'+a[4]+'">'+esc(a[0])+'</text>');});
  o.push('<text x="'+(ml+(W-ml-mr)/2)+'" y="'+(H-10)+'" text-anchor="middle" class="eje">'+esc(D.tx)+' →</text>');
  o.push('<text x="13" y="'+((mt+H-mb)/2)+'" text-anchor="middle" class="eje" transform="rotate(-90 13 '+((mt+H-mb)/2)+')">'+esc(D.ty)+' →</text>');
  ser.forEach(function(z){var col=z.c,n=z.x.length;
   o.push('<g class="serie" data-t="'+esc(z.k)+'" data-k="'+esc(z.k)+'" data-g="'+esc(z.g)+'" data-tip="'+esc(z.tip)+'">');
   o.push('<polyline class="r" points="'+z.x.map(function(v,i){return X(v).toFixed(1)+','+Y(z.y[i]).toFixed(1);}).join(' ')+
    '" fill="none" stroke="'+col+'" stroke-width="1.6" stroke-opacity="0.55"/>');
   for(var i=1;i<n-1;i++)o.push('<circle class="r" cx="'+X(z.x[i]).toFixed(1)+'" cy="'+Y(z.y[i]).toFixed(1)+'" r="1.6" fill="'+col+'" fill-opacity="0.45"/>');
   o.push('<rect class="r" x="'+(X(z.x[0])-3.5).toFixed(1)+'" y="'+(Y(z.y[0])-3.5).toFixed(1)+'" width="7" height="7" fill="#fff" stroke="'+col+'" stroke-width="1.6"/>');
   var a=X(z.x[n-1]),b=Y(z.y[n-1]);
   o.push('<circle cx="'+a.toFixed(1)+'" cy="'+b.toFixed(1)+'" r="4.5" fill="'+col+'"/>');
   o.push(a>W-110?'<text x="'+(a-6).toFixed(1)+'" y="'+(b-5).toFixed(1)+'" text-anchor="end" class="etq">'+esc(z.k)+'</text>'
    :'<text x="'+(a+6).toFixed(1)+'" y="'+(b-5).toFixed(1)+'" class="etq">'+esc(z.k)+'</text>');
   o.push('</g>');});
  o.push('</svg>');cont.innerHTML=o.join('');
  var cj=document.getElementById('exp-'+id);if(cj&&!cj.hidden&&window.expFijar)window.expFijar(id,cj.getAttribute('data-k'));}
 window.rrgDibujar=dibujar;dibujar('mapa-motores');dibujar('mapa-macro');
 var pend=false;document.querySelectorAll('.fm').forEach(function(ch){ch.addEventListener('change',function(){if(pend)return;pend=true;
  requestAnimationFrame(function(){pend=false;dibujar('mapa-motores');});});});})();
document.querySelectorAll('table.ord').forEach(function(tb){tb.querySelectorAll('th').forEach(function(th,i){
 th.addEventListener('click',function(){var filas=Array.from(tb.querySelectorAll('tr')).slice(1);
  var asc=th.dataset.asc!=='1';th.dataset.asc=asc?'1':'0';
  filas.sort(function(a,b){var x=a.children[i].innerText,y=b.children[i].innerText;
   var nx=parseFloat(x.replace(/\./g,'').replace(',','.')),ny=parseFloat(y.replace(/\./g,'').replace(',','.'));
   if(!isNaN(nx)&&!isNaN(ny))return asc?nx-ny:ny-nx;return asc?x.localeCompare(y):y.localeCompare(x);});
  filas.forEach(function(f){f.parentNode.appendChild(f);});});});});
// ---- Explicación de la dinámica al pulsar un vector
(function(){var EXP={};try{EXP=JSON.parse(document.getElementById('exp-datos').textContent)||{};}catch(e){}
 function fijar(mapa,k){document.querySelectorAll('[data-exp="'+mapa+'"] svg.grafico').forEach(function(svg){
   svg.classList.toggle('fijando',!!k);
   svg.querySelectorAll('g.serie').forEach(function(g){g.classList.toggle('fija',!!k&&g.getAttribute('data-k')===k);});});}
 function sel(mapa){return document.querySelector('select.exp-sel[data-mapa="'+mapa+'"]');}
 function mostrar(mapa,k){var caja=document.getElementById('exp-'+mapa);var d=(EXP[mapa]||{})[k];if(!caja||!d)return;
  caja.innerHTML='<button class="cerrar-exp" aria-label="Cerrar">×</button>'+d;caja.hidden=false;caja.setAttribute('data-k',k);
  var s=sel(mapa);if(s)s.value=k;fijar(mapa,k);
  var r=caja.getBoundingClientRect();if(r.top<0||r.bottom>window.innerHeight)caja.scrollIntoView({behavior:'smooth',block:'nearest'});}
 function cerrar(mapa){var caja=document.getElementById('exp-'+mapa);if(caja){caja.hidden=true;caja.removeAttribute('data-k');}
  var s=sel(mapa);if(s)s.value='';fijar(mapa,null);}
 window.expFijar=fijar;
 // Serie más cercana al cursor (punto actual, rastro o línea), no la que esté dibujada encima
 function nums(s){return s.trim().split(/[\s,]+/).map(Number);}
 function trozos(g){var out={pt:null,lin:[]},c=g.querySelector('circle[r="4.5"]'),pl=g.querySelector('polyline.r'),pa=g.querySelector('path.l');
  if(c)out.pt=[+c.getAttribute('cx'),+c.getAttribute('cy')];
  if(pl&&!g.closest('.sin-rastro')){var v=nums(pl.getAttribute('points')),a=[];for(var i=0;i+1<v.length;i+=2)a.push([v[i],v[i+1]]);out.lin.push(a);}
  if(pa){pa.getAttribute('d').split('M').forEach(function(sp){if(!sp.trim())return;var a=[];
   sp.split('L').forEach(function(q){var v=nums(q);if(v.length>=2)a.push([v[0],v[1]]);});out.lin.push(a);});}
  return out;}
 function dseg(p,a,b){var dx=b[0]-a[0],dy=b[1]-a[1],L=dx*dx+dy*dy,t=L?((p[0]-a[0])*dx+(p[1]-a[1])*dy)/L:0;
  t=Math.max(0,Math.min(1,t));var x=a[0]+t*dx-p[0],y=a[1]+t*dy-p[1];return Math.sqrt(x*x+y*y);}
 function cercana(svg,ev,gEtq){var m=svg.getScreenCTM();if(!m)return null;var pt=svg.createSVGPoint();pt.x=ev.clientX;pt.y=ev.clientY;
  var p=pt.matrixTransform(m.inverse()),p2=[p.x,p.y],esc=Math.abs(m.a||1),gP=null,dP=Infinity,gL=null,dL=Infinity;
  svg.querySelectorAll('g.serie').forEach(function(g){if(g.style.display==='none')return;var z=trozos(g);
   if(z.pt){var d=Math.hypot(z.pt[0]-p2[0],z.pt[1]-p2[1]);if(d<dP){dP=d;gP=g;}}
   z.lin.forEach(function(a){for(var i=1;i<a.length;i++){var d=dseg(p2,a[i-1],a[i]);if(d<dL){dL=d;gL=g;}}});});
  // Prioridad: punto actual (círculo grande), etiqueta pulsada, rastros y líneas
  if(gP&&dP*esc<=9)return gP;if(gEtq)return gEtq;if(gL&&dL*esc<=14)return gL;if(gP&&dP*esc<=14)return gP;return null;}
 function serieEn(ev){var t=ev.target;if(!t.closest)return null;var c=t.closest('[data-exp]');if(!c)return null;
  var svg=t.closest('svg.grafico')||c.querySelector('svg.grafico');if(!svg)return null;
  var g=t.closest('g.serie'),etq=(g&&t.tagName.toLowerCase()==='text')?g:null;return {c:c,svg:svg,g:cercana(svg,ev,etq)};}
 var tip=null,mostrandoTip=false;
 document.querySelectorAll('[data-exp]').forEach(function(c){
  c.addEventListener('mousemove',function(ev){var r=serieEn(ev);if(!r)return;tip=tip||document.getElementById('tip');
   r.svg.querySelectorAll('g.serie.activa').forEach(function(x){if(x!==r.g)x.classList.remove('activa');});
   if(r.g){r.svg.classList.add('resaltando');r.g.classList.add('activa');
    if(tip){tip.textContent=r.g.getAttribute('data-tip')||'';tip.style.display='block';mostrandoTip=true;}}
   else{r.svg.classList.remove('resaltando');if(tip&&mostrandoTip){tip.style.display='none';mostrandoTip=false;}}});
  c.addEventListener('mouseleave',function(){c.querySelectorAll('svg.grafico').forEach(function(s){s.classList.remove('resaltando');
   s.querySelectorAll('g.serie.activa').forEach(function(x){x.classList.remove('activa');});});
   if(tip&&mostrandoTip){tip.style.display='none';mostrandoTip=false;}});
  c.addEventListener('click',function(ev){var t0=document.getElementById('tip');if(t0)t0.style.display='none';mostrandoTip=false;
   var r=serieEn(ev);if(!r||!r.g)return;var k=r.g.getAttribute('data-k');if(!k)return;
   var mapa=c.getAttribute('data-exp'),caja=document.getElementById('exp-'+mapa);
   if(caja&&!caja.hidden&&caja.getAttribute('data-k')===k)cerrar(mapa);else mostrar(mapa,k);});});
 document.addEventListener('click',function(ev){if(!ev.target.closest)return;
  var x=ev.target.closest('.cerrar-exp');if(x)cerrar(x.parentNode.getAttribute('data-mapa'));});
 window.addEventListener('scroll',function(){var t0=document.getElementById('tip');if(t0)t0.style.display='none';mostrandoTip=false;},{passive:true});
 document.querySelectorAll('select.exp-sel').forEach(function(s){s.addEventListener('change',function(){
  var mapa=s.getAttribute('data-mapa');if(s.value)mostrar(mapa,s.value);else cerrar(mapa);});});})();
// ---- Vista en base 100
(function(){var D=null;try{D=JSON.parse(document.getElementById('b100-datos').textContent);}catch(e){}
 var caja=document.getElementById('b100'),per=document.getElementById('b100-per');if(!caja||!per)return;
 var graf=caja.querySelector('.b100-graf'),rank=caja.querySelector('.b100-rank');
 function num(x,d){return x.toFixed(d).replace('.',',');}
 function pctTxt(v){var p=v-100;return (p>=0?'+':'−')+num(Math.abs(p),1)+'\u00a0%';}
 function fch(iso,dia){var p=iso.split('-');return dia?(p[2]+'-'+p[1]+'-'+p[0]):(p[1]+'-'+p[0]);}
 function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
 function visibles(){var v=[];document.querySelectorAll('.fm').forEach(function(c){if(c.checked&&D.s[c.value])v.push(c.value);});return v;}
 function dibujar(){if(caja.hidden)return;
  if(!D||!D.f||D.f.length<2){graf.innerHTML='<p class="nota">Sin datos suficientes.</p>';rank.innerHTML='';return;}
  var N=parseInt(per.value,10)||24,F=D.f.slice(-(N+1)),n=F.length;
  var tks=visibles();if(D.s[D.ref])tks.push(D.ref);
  var L=[];tks.forEach(function(t){var a=D.s[t].slice(-(N+1)),b=null,i0=0;
   for(var i=0;i<a.length;i++){if(a[i]!=null){b=a[i];i0=i;break;}}if(b==null)return;
   var v=a.map(function(x){return x==null?null:x/b*100;}),fin=null;
   for(var j=v.length-1;j>=0;j--){if(v[j]!=null){fin=v[j];break;}}
   L.push({t:t,v:v,i0:i0,fin:fin,ref:t===D.ref});});
  var lo=100,hi=100;L.forEach(function(l){l.v.forEach(function(x){if(x!=null){if(x<lo)lo=x;if(x>hi)hi=x;}});});
  var ll=Math.log(lo),lh=Math.log(hi),pad=(lh-ll)*0.06||0.02;ll-=pad;lh+=pad;
  var W=760,H=430,ml=44,mr=114,mt=12,mb=28;
  function X(i){return ml+i/(n-1)*(W-ml-mr);}
  function Y(v){return mt+(lh-Math.log(v))/(lh-ll)*(H-mt-mb);}
  var vmin=Math.exp(ll),vmax=Math.exp(lh),cand=[];
  if(vmax/vmin<2.2){var span=vmax-vmin,steps=[1,2,2.5,5,10,20,25,50,100],st=100;
   for(var s=0;s<steps.length;s++){if(span/steps[s]<=7){st=steps[s];break;}}
   for(var t=Math.ceil(vmin/st)*st;t<=vmax;t+=st)cand.push(+t.toFixed(4));}
  else{[1,1.2,1.5,2,2.5,3,4,5,6,7,8].forEach(function(m){for(var d=0.1;d<=1e4;d*=10){var t=+(m*d).toPrecision(3);if(t>=vmin&&t<=vmax)cand.push(t);}});}
  var ticks=[100];
  [cand.filter(function(t){return t>100;}).sort(function(a,b){return a-b;}),
   cand.filter(function(t){return t<100;}).sort(function(a,b){return b-a;})].forEach(function(lst){var last=100;
   lst.forEach(function(t){if(Math.abs(Y(t)-Y(last))>=20){ticks.push(t);last=t;}});});
  var o=['<svg viewBox="0 0 '+W+' '+H+'" class="grafico b100svg" role="img">'];
  ticks.forEach(function(t){var y=Y(t).toFixed(1);
   o.push('<line x1="'+ml+'" x2="'+(W-mr)+'" y1="'+y+'" y2="'+y+'" stroke="'+(t===100?'#7d8a9c':'#d5dde7')+'" stroke-width="1"'+(t===100?' stroke-dasharray="3 3"':'')+'/>');
   o.push('<text x="'+(ml-6)+'" y="'+(+y+3.5)+'" text-anchor="end" class="ytk">'+String(t).replace('.',',')+'</text>');});
  var nx=Math.min(6,n);for(var k=0;k<nx;k++){var ix=Math.round(k*(n-1)/(nx-1)),xx=X(ix).toFixed(1);
   o.push('<line x1="'+xx+'" x2="'+xx+'" y1="'+(H-mb)+'" y2="'+(H-mb+4)+'" stroke="#7d8a9c"/>');
   o.push('<text x="'+xx+'" y="'+(H-mb+17)+'" text-anchor="'+(k===0?'start':k===nx-1?'end':'middle')+'" class="fdt">'+fch(F[ix],N<=52)+'</text>');}
  var et=L.map(function(l){return {t:l.t,y:Y(l.fin)};}).sort(function(a,b){return a.y-b.y;}),gap=11;
  for(var q=1;q<et.length;q++)if(et[q].y<et[q-1].y+gap)et[q].y=et[q-1].y+gap;
  if(et.length&&et[et.length-1].y>H-mb){et[et.length-1].y=H-mb;for(q=et.length-2;q>=0;q--)if(et[q].y>et[q+1].y-gap)et[q].y=et[q+1].y-gap;}
  var pos={};et.forEach(function(z){pos[z.t]=z.y;});
  L.sort(function(a,b){return (a.ref?1:0)-(b.ref?1:0);});
  L.forEach(function(l){var d='',pen=false;l.v.forEach(function(x,i){if(x==null){pen=false;return;}
    d+=(pen?'L':'M')+X(i).toFixed(1)+','+Y(x).toFixed(1);pen=true;});
   var col=l.ref?'#1b2a3a':(D.c[l.t]||'#555'),nom=D.n[l.t]||l.t,ye=Y(l.fin),yl=pos[l.t];
   var tip=l.t+' · '+nom+': '+pctTxt(l.fin)+' en el periodo'+(l.i0>0?' (sus datos empiezan más tarde)':'')+'.'+(l.ref?' Referencia de liquidez.':' Pulsa para ver su dinámica.');
   o.push('<g class="serie'+(l.ref?' ref':'')+'"'+(l.ref?'':' data-k="'+esc(l.t)+'"')+' data-tip="'+esc(tip)+'">');
   o.push('<path class="l" d="'+d+'" fill="none" stroke="'+col+'" stroke-width="'+(l.ref?2.2:1.5)+'"'+(l.ref?' stroke-dasharray="6 4"':' stroke-opacity="0.85"')+'/>');
   o.push('<circle cx="'+(W-mr).toFixed(1)+'" cy="'+ye.toFixed(1)+'" r="2" fill="'+col+'"/>');
   if(Math.abs(yl-ye)>1.5)o.push('<line x1="'+(W-mr+3).toFixed(1)+'" y1="'+ye.toFixed(1)+'" x2="'+(W-mr+20).toFixed(1)+'" y2="'+yl.toFixed(1)+'" stroke="'+col+'" stroke-width="0.7" stroke-opacity="0.55" stroke-dasharray="1.5 2"/>');
   o.push('<text x="'+(W-mr+22)+'" y="'+(yl+3.5).toFixed(1)+'" class="etq2" fill="'+col+'">'+esc(l.t)+' '+pctTxt(l.fin)+'</text></g>');});
  o.push('</svg>');graf.innerHTML=o.join('');
  var refFin=null;L.forEach(function(l){if(l.ref)refFin=l.fin;});
  var R=L.slice().sort(function(a,b){return b.fin-a.fin;});
  rank.innerHTML='<p class="nota">Rentabilidad en el periodo (<span class="fd">'+fch(F[0],true)+'</span> → <span class="fd">'+fch(F[n-1],true)+
   '</span>), de mayor a menor. Verde: mejor que las letras del Tesoro; rojo: peor.</p><ol>'+R.map(function(l){
   var col=l.ref?'#1b2a3a':(D.c[l.t]||'#555'),cls=l.ref||refFin==null?'':(l.fin>=refFin?'mas':'menos');
   return '<li'+(l.ref?' class="ref"':'')+'><span class="chip" style="background:'+col+'"></span><b>'+esc(l.t)+'</b> '+esc(D.n[l.t]||'')+
    ' <span class="v '+cls+'">'+pctTxt(l.fin)+'</span></li>';}).join('')+'</ol>';
  var c=document.getElementById('exp-mapa-motores');if(c&&!c.hidden&&window.expFijar)window.expFijar('mapa-motores',c.getAttribute('data-k'));}
 function vista(v){var b=v==='b100';
  document.querySelectorAll('.solo-mapa').forEach(function(x){x.hidden=b;});
  document.querySelectorAll('.solo-b100').forEach(function(x){x.hidden=!b;});
  var p=document.querySelector('.vista .periodo');if(p)p.hidden=!b;if(b)dibujar();}
 document.querySelectorAll('input[name="vista-motores"]').forEach(function(r){r.addEventListener('change',function(){if(r.checked)vista(r.value);});});
 per.addEventListener('change',dibujar);
 var pend=false;document.querySelectorAll('.fm').forEach(function(c){c.addEventListener('change',function(){if(pend)return;pend=true;
  requestAnimationFrame(function(){pend=false;dibujar();});});});
 var r0=document.querySelector('input[name="vista-motores"]:checked');if(r0&&r0.value==='b100')vista('b100');})();
</script>
</body></html>
"""

if __name__ == "__main__":
    main()
