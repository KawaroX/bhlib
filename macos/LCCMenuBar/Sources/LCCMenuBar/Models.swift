import Foundation

struct MeCurrent: Identifiable, Sendable {
    let id: String
    let areaId: String
    let seatNo: String
    let status: String
    let area: String
    let beginTime: String
    let endTime: String

    static func parse(jsonData: Data) -> MeCurrent? {
        guard
            let obj = try? JSONSerialization.jsonObject(with: jsonData) as? [String: Any]
        else { return nil }
        let active = (obj["active"] as? Bool) ?? true
        if active == false { return nil }

        func s(_ key: String) -> String { (obj[key] as? String) ?? "" }
        let areaId = String(describing: obj["area_id"] ?? "")
        let seatNo = String(describing: obj["seat_no"] ?? "")
        let status = String(describing: obj["status"] ?? "")
        let area = String(describing: obj["area"] ?? "")
        let beginTime = String(describing: obj["beginTime"] ?? "")
        let endTime = String(describing: obj["endTime"] ?? "")
        let deviceId = String(describing: obj["device_id"] ?? "")

        return MeCurrent(
            id: deviceId.isEmpty ? UUID().uuidString : deviceId,
            areaId: areaId,
            seatNo: seatNo,
            status: status,
            area: area,
            beginTime: beginTime,
            endTime: endTime
        )
    }
}

struct Seat: Identifiable, Sendable {
    let id: String
    let no: String
    let status: String
    let statusName: String

    var noPadded: String {
        let trimmed = no.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.count >= 4 { return trimmed }
        return String(repeating: "0", count: max(0, 4 - trimmed.count)) + trimmed
    }

    static func parseList(jsonData: Data) -> [Seat] {
        guard
            let root = try? JSONSerialization.jsonObject(with: jsonData) as? [String: Any],
            let data = root["data"] as? [String: Any],
            let list = data["list"] as? [[String: Any]]
        else { return [] }

        return list.compactMap { it in
            let id = String(describing: it["id"] ?? "")
            if id.isEmpty { return nil }
            let no = String(describing: it["no"] ?? "")
            let status = String(describing: it["status"] ?? "")
            let statusName = String(describing: it["status_name"] ?? "")
            return Seat(id: id, no: no, status: status, statusName: statusName)
        }.filter { $0.status == "1" }
    }
}

