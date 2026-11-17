// Apple Vision OCR for OTTO's cross-check (see ocr.py). Usage: vision_ocr <image>
// Prints the recognised text (German and English), one line per row of text.
import Foundation
import Vision

let url = URL(fileURLWithPath: CommandLine.arguments[1])
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.recognitionLanguages = ["de-DE", "en-US"]
request.usesLanguageCorrection = true
try VNImageRequestHandler(url: url, options: [:]).perform([request])

var boxes = (request.results ?? []).compactMap { observation -> (CGRect, String)? in
    guard let candidate = observation.topCandidates(1).first else { return nil }
    return (observation.boundingBox, candidate.string)
}
// Same row when the vertical centres are within half a box height; top to bottom.
boxes.sort { $0.0.midY > $1.0.midY }
var rows: [[(CGRect, String)]] = []
for box in boxes {
    if let first = rows.last?.first, abs(first.0.midY - box.0.midY) < min(first.0.height, box.0.height) * 0.5 {
        rows[rows.count - 1].append(box)
    } else {
        rows.append([box])
    }
}
for row in rows {
    print(row.sorted { $0.0.minX < $1.0.minX }.map { $0.1 }.joined(separator: "   "))
}
