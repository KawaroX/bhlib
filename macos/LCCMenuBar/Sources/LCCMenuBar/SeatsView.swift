import SwiftUI

struct SeatsView: View {
    @EnvironmentObject private var app: AppModel

    @State private var day: Date = Date()
    @State private var startTime: String = SeatsView.defaultStartTime()
    @State private var endTime: String = "23:00"
    @State private var search: String = ""

    var filteredSeats: [Seat] {
        let q = search.trimmingCharacters(in: .whitespacesAndNewlines)
        if q.isEmpty { return app.seats }
        return app.seats.filter { $0.no.contains(q) || $0.id.contains(q) }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            GroupBox("查询") {
                VStack(alignment: .leading, spacing: 8) {
                    HStack {
                        DatePicker("日期", selection: $day, displayedComponents: .date)
                        Spacer()
                    }
                    HStack {
                        TextField("开始 HH:MM", text: $startTime).textFieldStyle(.roundedBorder)
                        TextField("结束 HH:MM", text: $endTime).textFieldStyle(.roundedBorder)
                    }
                    HStack {
                        Button("刷新空闲座位") {
                            Task { await app.fetchSeats(day: day, startTime: startTime, endTime: endTime) }
                        }
                        Spacer()
                        Button("预约所选") {
                            Task { await app.bookSelectedSeat(day: day, startTime: startTime, endTime: endTime) }
                        }
                        .disabled(app.selectedSeatId == nil)
                    }
                }
            }

            GroupBox("空闲座位") {
                VStack(alignment: .leading, spacing: 8) {
                    TextField("搜索（座位号或 id）", text: $search)
                        .textFieldStyle(.roundedBorder)
                    List(filteredSeats, selection: $app.selectedSeatId) { seat in
                        HStack {
                            Text(seat.no.isEmpty ? "(无号)" : seat.no)
                                .frame(width: 60, alignment: .leading)
                            Text("id:\(seat.id)")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            Spacer()
                        }
                        .tag(seat.id)
                    }
                    .frame(minHeight: 180)
                }
            }
        }
    }

    private static func defaultStartTime() -> String {
        let f = DateFormatter()
        f.calendar = Calendar(identifier: .gregorian)
        f.locale = Locale(identifier: "zh_CN")
        f.timeZone = TimeZone.current
        f.dateFormat = "HH:mm"
        return f.string(from: Date())
    }
}

