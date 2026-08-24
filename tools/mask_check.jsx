/*
 * Wyciaga z pliku strukture warstw i KAZDA maske osobno.
 *
 * Why not through the flattened image: a layer covering the whole canvas hides
 * everything beneath it, so comparing composites passes vacuously - masks
 * nizej nie maja wtedy zadnego wplywu na result. Maske trzeba przeczytac
 * wprost.
 *
 * Jak: polecenie "duplikuj channel" wycelowane w channel masks laduje ja jako
 * zwykly channel alfa. Photoshop musi ja wtedy rozpakowac sam, wiec dostajemy
 * what it really sees, not what we wrote.
 *
 * Wejscie i output ida przez files JSON obok tego skryptu. Uruchamia sie to
 * z mask_check.py, nie recznie.
 */

#target photoshop

var TU = new File($.fileName).parent;
var WEJSCIE = new File(TU + "/.mask_check_in.json");
var WYJSCIE = new File(TU + "/.mask_check_out.json");

function czytaj(file) {
    file.open("r");
    file.encoding = "UTF-8";
    var tresc = file.read();
    file.close();
    return tresc;
}

function pisz(file, tresc) {
    file.open("w");
    file.encoding = "UTF-8";
    file.write(tresc);
    file.close();
}

/* ExtendScript has no JSON.stringify, hence this minimal serialiser. */
function jsonTekst(v) {
    return '"' + String(v)
        .replace(/\\/g, "\\\\")
        .replace(/"/g, '\\"')
        .replace(/[\r\n\t]/g, " ") + '"';
}

function json(v) {
    if (v === null || v === undefined) { return "null"; }
    if (typeof v === "number") { return String(v); }
    if (typeof v === "boolean") { return v ? "true" : "false"; }
    if (typeof v === "string") { return jsonTekst(v); }

    if (v instanceof Array) {
        var e = [];
        for (var i = 0; i < v.length; i++) { e.push(json(v[i])); }
        return "[" + e.join(",") + "]";
    }

    var p = [];
    for (var k in v) {
        if (v.hasOwnProperty(k)) { p.push(jsonTekst(k) + ":" + json(v[k])); }
    }
    return "{" + p.join(",") + "}";
}

function drzewo(kolekcja, sciezka, result) {
    for (var i = 0; i < kolekcja.length; i++) {
        var w = kolekcja[i];
        var name = sciezka + "/" + w.name;

        result.push({ warstwa: w, sciezka: name, typ: w.typename,
                     widoczna: w.visible });

        if (w.typename === "LayerSet") { drzewo(w.layers, name, result); }
    }
    return result;
}

function maskaDoAlfa(doc, warstwa) {
    doc.activeLayer = warstwa;

    var desc = new ActionDescriptor();
    var ref = new ActionReference();
    ref.putEnumerated(charIDToTypeID("Chnl"), charIDToTypeID("Chnl"),
                      charIDToTypeID("Msk "));
    desc.putReference(charIDToTypeID("null"), ref);
    desc.putString(charIDToTypeID("Nm  "), "mask_check");

    executeAction(charIDToTypeID("Dplc"), desc, DialogModes.NO);

    return doc.channels[doc.channels.length - 1];
}

function opcjeTiff() {
    var o = new TiffSaveOptions();
    o.imageCompression = TIFFEncoding.NONE;  /* so tifffile can read it without imagecodecs */
    o.layers = false;
    o.alphaChannels = false;
    o.embedColorProfile = false;
    return o;
}

function zapiszKanal(doc, channel, sciezkaPliku) {
    doc.activeChannels = [channel];
    doc.selection.selectAll();
    doc.selection.copy();
    doc.selection.deselect();

    var szary = app.documents.add(
        doc.width, doc.height, doc.resolution, "mask_check",
        NewDocumentMode.GRAYSCALE, DocumentFill.WHITE, 1,
        BitsPerChannelType.SIXTEEN
    );

    app.activeDocument = szary;
    szary.paste();
    szary.flatten();
    szary.saveAs(new File(sciezkaPliku), opcjeTiff(), true, Extension.LOWERCASE);
    szary.close(SaveOptions.DONOTSAVECHANGES);

    app.activeDocument = doc;
}

/* We write the config file ourselves, so eval is safe here. */
var cfg = eval("(" + czytaj(WEJSCIE) + ")");
var raport = [];

for (var f = 0; f < cfg.files.length; f++) {
    var sciezka = cfg.files[f];
    var wpis = { file: sciezka, otwarty: false };

    try {
        var doc = app.open(new File(sciezka));

        wpis.otwarty = true;
        wpis.width = doc.width.value;
        wpis.height = doc.height.value;
        wpis.tryb = String(doc.mode);
        wpis.bity = String(doc.bitsPerChannel);

        var warstwy = drzewo(doc.layers, "", []);
        var label = [];
        var masks = [];

        for (var i = 0; i < warstwy.length; i++) {
            label.push({ sciezka: warstwy[i].sciezka, typ: warstwy[i].typ,
                        widoczna: warstwy[i].widoczna });

            var channel = null;

            try { channel = maskaDoAlfa(doc, warstwy[i].warstwa); }
            catch (e) { continue; }

            var h = channel.histogram;
            var total = 0, niepuste = 0;

            for (var b = 0; b < h.length; b++) {
                total += h[b] * b;
                if (h[b] > 0) { niepuste++; }
            }

            var wpisMaski = { sciezka: warstwy[i].sciezka, total: total,
                              koszyki: niepuste, histogram: h.join(","),
                              piksele: null };

            if (cfg.piksele && niepuste >= cfg.threshold) {
                var etykieta = warstwy[i].sciezka.replace(/[^A-Za-z0-9]+/g, "_");
                var target = cfg.katalog + "/px_" + f + etykieta + ".tif";

                zapiszKanal(doc, channel, target);
                wpisMaski.piksele = target;
            }

            masks.push(wpisMaski);
            channel.remove();
        }

        wpis.warstwy = label;
        wpis.masks = masks;

        doc.close(SaveOptions.DONOTSAVECHANGES);
    } catch (e) {
        wpis.error = String(e);
    }

    raport.push(wpis);
}

pisz(WYJSCIE, json(raport));

"done: " + raport.length + " plikow";
