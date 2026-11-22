# Loadcombie · Lastkomb

Lastkomb är ett svenskt skrivbordsprogram för lastkombinationer längs en bro. Det läser oberoende linjära laststeg från Abaqus, beräknar STR enligt uppgift AF2203, avsnitt 3.5, och bevarar den lastplacering som gav varje extremvärde. Programmet körs lokalt.

## Starta och importera

- **Windows x64.** Packa upp hela Windows-zippen och öppna `Lastkomb.exe` i mappen `Lastkomb`. Python och Qt ingår.
- **Mac med Apple Silicon och macOS 15 eller senare.** Hämta DMG-filen från den senaste releasen, öppna den och dra **Lastkomb** till **Program**. [Ladda ner för Mac](https://github.com/gitterkorso/Loadcombie/releases/latest)
- Första starten visar ett **syntetiskt räkneexempel** med huvudbalk, plåt och influenslinje. Välj **Importera CSV…**, **+ Mapp** eller dra en mapp till fönstret. Den första egna importen ersätter exemplet. Efterföljande importer lägger till data. **Mer → Öppna räkneexempel** öppnar exemplet igen.

Välj del i trädet, storhet ovanför diagrammet och beräkningsläge uppe till höger. För plåtar väljer du även SPOS eller SNEG. Momentdiagram visar positivt moment nedåt. Klicka eller dra i diagrammet för att avläsa alla storheter vid närmaste snitt. Klicka på en kurvpunkt eller en cell i max/min-kolumnen för att se styrande ekvation, laststeg, delbidrag och samtidiga värden. Skriv intervallgränser och tryck Retur, eller håll **Skift** och dra vågrätt i diagrammet. **Hela** återställer intervallet.

Öppna [användarmanualen](lastkomb/manual.html) via **Mer → Användarmanual** eller **F1**. Den innehåller ett genomräknat exempel, anvisningar för CSV-import och en guide till snitt, lastregler, kontroller och export.

**Kurvor** lägger enskilda ofaktorerade laststeg ovanpå omhöljet. **Visa kombination som kurva** tillämpar den valda punktens faktorvektor på hela delen. En sådan kurva visar en enda fysisk lastplacering. Omhöljets olika punkter kan ha olika lastplaceringar. Fästa kurvor rensas när lastreglerna ändras. I **Lastregler → Modell** kan du ange lägen för lager, hängare och fordonets tunga axel och bekräfta momentaxlarna för den aktuella delen.

## CSV-underlag

Semikolon avgränsar kolumner. Programmet läser UTF-8 med eller utan BOM och Windows-1252. Decimaltecknet får vara komma eller punkt. Kolumner matchas på rubrik. Exempel på rubriker visas nedan.

| Resultat | Exempel på filnamn | Rubrik |
| --- | --- | --- |
| Bred balkfil | `HUVUDBALK_SM1.csv` | `Element;Nod;x [m];y [m];z [m];G_SELF;G_SURF;Q_PED_01;H_PED_01` |
| Hängare | `HANG_SF1.csv` | Samma som balkfilen, med extra kolumn `Hängare x [m]` |
| Plåtsnitt | `PLAT_Y0_LANGS.csv` | `Steg;Sida;Läge [m];x [m];y [m];S11 [MPa];S22 [MPa];S12 [MPa]` |
| Influenslinje | `IL_huvudbalk.csv` | `Steg;Lastens x [m];SF1 [kN/kN];SM1 [kNm/kN];SM2 [kNm/kN]` |
| Extra nodresultat | `BROBANA_U3.csv`, `LAGER_RF3.csv` | `Nod;x [m];G_SELF;G_SURF;Q_PED_01;H_PED_01` |

För breda filer identifierar suffixet storheten. Filer för samma del och olika storheter kopplas ihop via punktidentiteten, oberoende av radordning. Behåll samma identitetskolumner i dessa filer. Två olika element vid samma x kan ha olika värden.

Utan uttrycklig enhet används m för koordinater och förskjutningar, kN för krafter, kNm för moment och MPa för spänning. Om exporten använder andra enheter kan de anges i rubriken, exempelvis `G_SELF [mm]` i en förskjutningsfil. För reaktions- och förskjutningskontroller passar separata nodbaserade delar som `LAGER` och `BROBANA`.

**Importkontroll** visar saknade steg, motstridiga dubbletter och ogiltiga värden. Ofullständiga punkter utesluts från det aktuella omhöljet. Saknas hela laststeg går det att studera det reducerade urvalet. Kontrollera urvalet och avvikelserna före rapportexport.

## Regler och tolkningar

- Kursuppgiftens tabell 3.2 ger γG = 1,35/1,00, γQ = 1,50/0, ψ₀ = 0,4 för gångtrafik och ψ₀ = 0 för servicefordonet. Avsnitt 3.5 ger ξ = 0,89. Förvalet har en gemensam permanent grupp och γd = 1,00.
- **Egna G-faktorer per last.** Öppna **Lastregler → Steg**, markera laststeget och välj **Egen G-grupp för markerat steg**. Ange γ ogynnsam, γ gynnsam, ξ och γd och klicka på **Tillämpa**. För att låta flera steg dela faktorer, skapa en grupp under **G-grupper** och välj samma grupp för dessa steg under **Steg**.
- Vid ett teckenbestämt resultat räknar programmet först samman gruppens bidrag i snittet. För största moment är ett positivt bidrag ogynnsamt och för minsta moment är ett negativt bidrag ogynnsamt. Varje grupp bedöms separat. Den ogynnsamma faktorn är γd × γ ogynnsam i 6.10a och ξ × γd × γ ogynnsam i 6.10b. Den gynnsamma faktorn är γ gynnsam i båda. För von Mises provas gruppernas tillåtna faktorer i kombination.
- Gångtrafik och servicefordon utesluter varandra i förvalet. V1–V5 är alternativa lägen. För ogynnsam gångtrafik provas båda gemensamma riktningarna och fälten väljs på deras sammanlagda vertikala och horisontella effekt. Även fall utan trafik ingår. Tillåter du samtidiga trafikgrupper växlar huvudlasten i 6.10b.
- Avsnitt 3.4.3 anger H som 10 % av belastad gånglast och 60 % av fordonslasten. H-steg ska redan innehålla dessa krafter. Programmet lägger inte på procentsatsen igen. Stegen måste vara oberoende lastbidrag i samma lokala axelsystem. Historiska totaler från successivt pålastade analyssteg behöver först omvandlas till oberoende bidrag.
- Kravspecifikationen använder fält 01–10 med P1 = 02–09 och P2 = 02–05. Placeringsritningen daterad 17 november använder 00–09. Kontrollera kopplingen i **Lastregler → Steg** och justera P1/P2 till den egna exporten.
- Vid x = 15 m har V2 och V4 båda den tunga axeln vid 15 m och den lätta vid 12 m enligt placeringsritningen. De skiljer sig i sidled. Välj ett faktiskt spegelfall för symmetrikontrollen.
- Diagram och tabeller visar normalkraft som **N (SF1)**. Grundinställningen för böjmoment är **M<sub>y</sub> (SM1)** och **M<sub>z</sub> (SM2)**. Under **Lastregler → Modell** kan du byta momentaxlar för en del. Tecknen följer den importerade Abaqus-modellen.
- För plåt kombineras S11, S22 och S12 för varje tillåten lastplacering före beräkning av von Mises. En negativ minsta huvudspänning i någon kombination markeras som tryck. Flaggan är en konservativ signal för granskning. Lokal och global bucklingsbärförmåga enligt uppgiftens avsnitt 6.4 behöver kontrolleras separat. Saknas spänningskomponenter visas tryckstatus som **Okänt**.
- Kontrollerna för **L/400** och **reaktionsjämvikt** behöver en vertikal U-komponent och teoretisk spännvidd respektive RF-värden för alla stöd och yttre lastresultant. De ordinarie 15 exportfilerna innehåller snittkrafter, spänningar och influenslinjer.
- För **Fordonsöverfart** används en komplett influenslinje från det aktuella snittet för en nedåtriktad enhetslast fördelad över rätt hjulspår. Ange area, vald fiber och detaljkategori. Extrapolationen använder noll utanför bron och kräver noll i ändpunkterna. Förvalen för lastplaceringen är 80 kN vid x och 40 kN vid x − 3 m. Cykelräkningen gäller en upprepad vertikal passage i positiv x-riktning. Bromskraft kräver ett separat lastförlopp. Programmet visar spänningsvidder, skadeandel per passage och helt antal passager till D = 1.

## Projekt och rapport

**Spara** skapar en flyttbar `.lastkomb` med original-CSV, SHA-256-kontrollsummor, kopplingar, regler, urval, fästa kurvor och diagraminställningar. Programmet räknar om resultat mot inbäddade data när projektet öppnas. Har källfiler ändrats kan du läsa in de nya versionerna med knappen i granskaren.

Äldre projekt öppnas med sina sparade regler. Projekt med egna G-grupper sparas i formatversion 2 och kräver en appversion som stöder dessa grupper.

**Exportera tabell…** skriver punktvärden, en kompakt redovisning med varje storhets globala max/min i valt intervall, samtliga punktextremer med samtidiga värden och beräkningsvillkor. Valda lastfall och fästa kombinationer exporteras också. I Excel ligger tabellerna på olika blad. Som CSV får de separata filnamn bredvid den valda filen. Influenslinjer exporteras som ordinater. **Spara figur…** ger PNG, SVG eller PDF. Mörkt tema, ångra/gör om och tangentbordsgenvägar finns i **Mer**.

## Verifiering

Arbetsboken [Handkontroll.xlsx](Handkontroll.xlsx) räknar oberoende på det syntetiska exemplets M<sub>y</sub> (SM1) i huvudbalken vid x = 15 m. Arbetsboken har räknats om i LibreOffice och jämförts med en separat genomräkning av fältkombinationerna.

| Ekvation | Max [kNm] | Min [kNm] |
| --- | ---: | ---: |
| 6.10a | 106,4202575226 | 63,8668221758 |
| 6.10b | 150,0975000000 | 62,1670554395 |

Max och min från 6.10b styr i denna punkt. De ursprungliga steg 10-filerna fanns inte i underlaget. Verifiering för den faktiska bron kräver samma jämförelse med dess exporter, kontroll av de lokala axlarna och bekräftelse av fältnumreringen.

## Köra och bygga från källkod

Testad källmiljö använder Python 3.12. Skapa en virtuell miljö, aktivera den och kör följande i projektmappen.

```shell
python -m pip install -r requirements.txt
python -m lastkomb
```

För tester, installera även `pytest` och kör `python -m pytest`. Beräkningsmotorn använder NumPy. PySide6 ger skrivbordsgränssnittet, Matplotlib hanterar både interaktiva diagram och rapportfigurer, och openpyxl skriver Excel-filer.

Mac-paketet byggs lokalt med PyInstaller. Installera `pyinstaller` och `pillow` i miljön. Kör följande kommando för en egen DMG-fil.

```sh
python packaging/build_macos.py --build build-mac --dmg Lastkomb-Mac.dmg
```

För distribution kan du ange en Developer ID Application-identitet med `--sign-identity` och den lägsta macOS-version som stöds av de paketerade biblioteken med `--minimum-macos`. Version 0.1.1 byggdes med CPython 3.12.16, PySide6 6.10.3 och PyInstaller 6.22.3 för arm64.

Windows-paketet byggs med det inbäddade Python för x64, låsta binärpaket och en liten körbar startfil. Installera `ziglang==0.13.0` och kör `python packaging/build_windows.py --cache build-cache --out Lastkomb-Windows-x64.zip`. Nedladdning behövs endast vid bygge. Den färdiga appen körs offline.
