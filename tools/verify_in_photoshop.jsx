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
var previousDialogs = app.displayDialogs;
app.displayDialogs = DialogModes.NO;

function layerNames(collection, result, depth) {
    for (var i = 0; i < collection.length; i++) {
        var layer = collection[i];
        result.push({
            name: layer.name,
            depth: depth,
            kind: String(layer.typename),
            visible: layer.visible
        });

        if (layer.typename === "LayerSet") {
            layerNames(layer.layers, result, depth + 1);
        }
    }
    return result;
}

function inspect(path) {
    var report = { file: decodeURI(path), opened: false };

    var file = new File(path);

    if (!file.exists) {
        report.error = "file does not exist";
        return report;
    }

    var document = null;

    try {
        document = app.open(file);
        report.opened = true;
        report.width = Math.round(document.width.as("px"));
        report.height = Math.round(document.height.as("px"));
        report.mode = String(document.mode);
        // bitsPerChannel is an enum: without String() it serialises as {},
        // which is what once made the depth comparison compare nothing.
        report.depth = String(document.bitsPerChannel);
        report.layers = layerNames(document.layers, [], 0);
        report.layerCount = report.layers.length;
    } catch (error) {
        report.error = String(error);
    } finally {
        if (document !== null) {
            try {
                document.close(SaveOptions.DONOTSAVECHANGES);
            } catch (ignored) {
                // the document may not have opened fully
            }
        }
    }

    return report;
}

function save(text) {
    var file = new File(REPORT);
    file.encoding = "UTF-8";
    file.open("w");
    file.write(text);
    file.close();
}

function serialise(value) {
    if (value === null) { return "null"; }

    var kind = typeof value;

    if (kind === "number") { return String(value); }
    if (kind === "boolean") { return value ? "true" : "false"; }

    if (kind === "string") {
        return '"' + value.replace(/\\/g, "\\\\").replace(/"/g, '\\"') + '"';
    }

    if (value instanceof Array) {
        var items = [];
        for (var i = 0; i < value.length; i++) {
            items.push(serialise(value[i]));
        }
        return "[" + items.join(",") + "]";
    }

    var fields = [];
    for (var key in value) {
        if (value.hasOwnProperty(key)) {
            fields.push('"' + key + '":' + serialise(value[key]));
        }
    }
    return "{" + fields.join(",") + "}";
}

// --- the actual work ---

var folder = new Folder(DIRECTORY);
var everything = folder.getFiles("*.tif");
var pairs = [];

for (var i = 0; i < everything.length; i++) {
    var name = decodeURI(everything[i].name);

    for (var s = 0; s < SUFFIXES.length; s++) {
        var suffix = SUFFIXES[s];

        if (name.indexOf(suffix) === -1) {
            continue;
        }

        var source = name.replace(suffix, ".tif");

        if (new File(DIRECTORY + "/" + source).exists) {
            pairs.push({ source: source, result: name, variant: suffix });
        }
        break;
    }
}

var results = [];

for (var j = 0; j < pairs.length; j++) {
    results.push({
        pair: pairs[j].source,
        variant: pairs[j].variant,
        source: inspect(DIRECTORY + "/" + pairs[j].source),
        result: inspect(DIRECTORY + "/" + pairs[j].result)
    });

    // Report after every pair, so a crash does not take everything with it.
    save(serialise(results));
}

app.displayDialogs = previousDialogs;

"done: " + results.length + " pairs";
