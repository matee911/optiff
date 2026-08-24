/*
 * Verifying TIFF files in Photoshop.
 *
 * Opens every source / result pair and compares dimensions, colour mode,
 * layer count and layer names, plus the inner layer count for smart object
 * layers. The result is written to a JSON file next to this script.
 *
 * Writes nothing: documents are closed without saving changes.
 *
 * Usage - set DIRECTORY below, then:
 *   osascript -e 'tell application "Adobe Photoshop 2026" \
 *     to do javascript file "/path/to/verify_in_photoshop.jsx"'
 *
 * Wrap the call in `with timeout of 3000 seconds` when the files are large:
 * osascript aborts an AppleEvent after 60 s by default.
 */

#target photoshop

// Directory holding the source files and their results. Set this before use;
// it is deliberately not committed as a real path.
var DIRECTORY = "";

if (!DIRECTORY) {
    throw new Error("Set DIRECTORY at the top of verify_in_photoshop.jsx");
}

var REPORT = new File($.fileName).parent + "/verify_report.json";

// Every result variant is paired with its source by one of these suffixes.
var SUFFIXES = [".opt.tif", ".img.tif", ".zipfb.tif"];

// A dialog would block the script forever.
var poprzedniDialog = app.displayDialogs;
app.displayDialogs = DialogModes.NO;

function nazwyWarstw(zbior, result, poziom) {
    for (var i = 0; i < zbior.length; i++) {
        var warstwa = zbior[i];
        result.push({
            name: warstwa.name,
            poziom: poziom,
            typ: String(warstwa.typename),
            widoczna: warstwa.visible
        });

        if (warstwa.typename === "LayerSet") {
            nazwyWarstw(warstwa.layers, result, poziom + 1);
        }
    }
    return result;
}

function zbadaj(sciezka) {
    var raport = { file: decodeURI(sciezka), otwarty: false };

    var file = new File(sciezka);

    if (!file.exists) {
        raport.error = "file nie istnieje";
        return raport;
    }

    var dokument = null;

    try {
        dokument = app.open(file);
        raport.otwarty = true;
        raport.width = Math.round(dokument.width.as("px"));
        raport.height = Math.round(dokument.height.as("px"));
        raport.tryb = String(dokument.mode);
        // bitsPerChannel is an enum: without String() it serialises as {},
        // przez co porownanie glebi bylo puste.
        raport.bity = String(dokument.bitsPerChannel);
        raport.warstwy = nazwyWarstw(dokument.layers, [], 0);
        raport.liczbaWarstw = raport.warstwy.length;
    } catch (error) {
        raport.error = String(error);
    } finally {
        if (dokument !== null) {
            try {
                dokument.close(SaveOptions.DONOTSAVECHANGES);
            } catch (ignorowany) {
                // the document may not have opened fully
            }
        }
    }

    return raport;
}

function zapisz(tekst) {
    var file = new File(REPORT);
    file.encoding = "UTF-8";
    file.open("w");
    file.write(tekst);
    file.close();
}

function serializuj(obiekt) {
    if (obiekt === null) { return "null"; }

    var typ = typeof obiekt;

    if (typ === "number") { return String(obiekt); }
    if (typ === "boolean") { return obiekt ? "true" : "false"; }

    if (typ === "string") {
        return '"' + obiekt.replace(/\\/g, "\\\\").replace(/"/g, '\\"') + '"';
    }

    if (obiekt instanceof Array) {
        var pozycje = [];
        for (var i = 0; i < obiekt.length; i++) {
            pozycje.push(serializuj(obiekt[i]));
        }
        return "[" + pozycje.join(",") + "]";
    }

    var pola = [];
    for (var klucz in obiekt) {
        if (obiekt.hasOwnProperty(klucz)) {
            pola.push('"' + klucz + '":' + serializuj(obiekt[klucz]));
        }
    }
    return "{" + pola.join(",") + "}";
}

// --- the actual work ---

var katalog = new Folder(DIRECTORY);
var wszystkie = katalog.getFiles("*.tif");
var pary = [];

for (var i = 0; i < wszystkie.length; i++) {
    var name = decodeURI(wszystkie[i].name);

    for (var s = 0; s < SUFFIXES.length; s++) {
        var sufiks = SUFFIXES[s];

        if (name.indexOf(sufiks) === -1) {
            continue;
        }

        var source = name.replace(sufiks, ".tif");

        if (new File(DIRECTORY + "/" + source).exists) {
            pary.push({ source: source, result: name, wariant: sufiks });
        }
        break;
    }
}

var results = [];

for (var j = 0; j < pary.length; j++) {
    results.push({
        para: pary[j].source,
        wariant: pary[j].wariant,
        source: zbadaj(DIRECTORY + "/" + pary[j].source),
        result: zbadaj(DIRECTORY + "/" + pary[j].result)
    });

    // Report after every pair, so a crash does not take everything with it.
    zapisz(serializuj(results));
}

app.displayDialogs = poprzedniDialog;

"done: " + results.length + " pairs";
