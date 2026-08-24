/*
 * Pulls the layer tree out of a file, plus EVERY mask on its own.
 *
 * Why not through the flattened image: a layer covering the whole canvas hides
 * everything beneath it, so comparing composites passes vacuously - the masks
 * underneath have no say in the result. Each mask has to be read directly.
 *
 * How: the "duplicate channel" command aimed at the mask channel lands it as
 * an ordinary alpha channel. Photoshop then has to decompress it itself, so
 * what we get is what it really sees, not what we wrote.
 *
 * Input and output travel through JSON files sitting next to this script. It
 * is started from mask_check.py, not by hand.
 */

#target photoshop

var HERE = new File($.fileName).parent;
var INPUT = new File(HERE + "/.mask_check_in.json");
var OUTPUT = new File(HERE + "/.mask_check_out.json");

function readFile(file) {
    file.open("r");
    file.encoding = "UTF-8";
    var content = file.read();
    file.close();
    return content;
}

function writeFile(file, content) {
    file.open("w");
    file.encoding = "UTF-8";
    file.write(content);
    file.close();
}

/* ExtendScript has no JSON.stringify, hence this minimal serialiser. */
function jsonString(v) {
    return '"' + String(v)
        .replace(/\\/g, "\\\\")
        .replace(/"/g, '\\"')
        .replace(/[\r\n\t]/g, " ") + '"';
}

function json(v) {
    if (v === null || v === undefined) { return "null"; }
    if (typeof v === "number") { return String(v); }
    if (typeof v === "boolean") { return v ? "true" : "false"; }
    if (typeof v === "string") { return jsonString(v); }

    if (v instanceof Array) {
        var e = [];
        for (var i = 0; i < v.length; i++) { e.push(json(v[i])); }
        return "[" + e.join(",") + "]";
    }

    var p = [];
    for (var k in v) {
        if (v.hasOwnProperty(k)) { p.push(jsonString(k) + ":" + json(v[k])); }
    }
    return "{" + p.join(",") + "}";
}

function walkTree(collection, path, result) {
    for (var i = 0; i < collection.length; i++) {
        var item = collection[i];
        var name = path + "/" + item.name;

        result.push({ layer: item, path: name, kind: item.typename,
                      visible: item.visible });

        if (item.typename === "LayerSet") { walkTree(item.layers, name, result); }
    }
    return result;
}

function maskToAlpha(doc, layer) {
    doc.activeLayer = layer;

    var desc = new ActionDescriptor();
    var ref = new ActionReference();
    ref.putEnumerated(charIDToTypeID("Chnl"), charIDToTypeID("Chnl"),
                      charIDToTypeID("Msk "));
    desc.putReference(charIDToTypeID("null"), ref);
    desc.putString(charIDToTypeID("Nm  "), "mask_check");

    executeAction(charIDToTypeID("Dplc"), desc, DialogModes.NO);

    return doc.channels[doc.channels.length - 1];
}

function tiffOptions() {
    var o = new TiffSaveOptions();
    o.imageCompression = TIFFEncoding.NONE;  /* so tifffile can read it without imagecodecs */
    o.layers = false;
    o.alphaChannels = false;
    o.embedColorProfile = false;
    return o;
}

function saveChannel(doc, channel, targetPath) {
    doc.activeChannels = [channel];
    doc.selection.selectAll();
    doc.selection.copy();
    doc.selection.deselect();

    var grey = app.documents.add(
        doc.width, doc.height, doc.resolution, "mask_check",
        NewDocumentMode.GRAYSCALE, DocumentFill.WHITE, 1,
        BitsPerChannelType.SIXTEEN
    );

    app.activeDocument = grey;
    grey.paste();
    grey.flatten();
    grey.saveAs(new File(targetPath), tiffOptions(), true, Extension.LOWERCASE);
    grey.close(SaveOptions.DONOTSAVECHANGES);

    app.activeDocument = doc;
}

/* We write the config file ourselves, so eval is safe here. */
var cfg = eval("(" + readFile(INPUT) + ")");
var report = [];

for (var f = 0; f < cfg.files.length; f++) {
    var path = cfg.files[f];
    var entry = { file: path, opened: false };

    try {
        var doc = app.open(new File(path));

        entry.opened = true;
        entry.width = doc.width.value;
        entry.height = doc.height.value;
        entry.mode = String(doc.mode);
        entry.depth = String(doc.bitsPerChannel);

        var layers = walkTree(doc.layers, "", []);
        var tree = [];
        var masks = [];

        for (var i = 0; i < layers.length; i++) {
            tree.push({ path: layers[i].path, kind: layers[i].kind,
                        visible: layers[i].visible });

            var channel = null;

            try { channel = maskToAlpha(doc, layers[i].layer); }
            catch (e) { continue; }

            var h = channel.histogram;
            var total = 0, used = 0;

            for (var b = 0; b < h.length; b++) {
                total += h[b] * b;
                if (h[b] > 0) { used++; }
            }

            var maskEntry = { path: layers[i].path, total: total,
                              buckets: used, histogram: h.join(","),
                              pixels: null };

            if (cfg.pixels && used >= cfg.threshold) {
                var label = layers[i].path.replace(/[^A-Za-z0-9]+/g, "_");
                var target = cfg.directory + "/px_" + f + label + ".tif";

                saveChannel(doc, channel, target);
                maskEntry.pixels = target;
            }

            masks.push(maskEntry);
            channel.remove();
        }

        entry.layers = tree;
        entry.masks = masks;

        doc.close(SaveOptions.DONOTSAVECHANGES);
    } catch (e) {
        entry.error = String(e);
    }

    report.push(entry);
}

writeFile(OUTPUT, json(report));

"done: " + report.length + " files";
