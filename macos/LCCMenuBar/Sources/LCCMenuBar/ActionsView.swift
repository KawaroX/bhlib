import SwiftUI

struct ActionsView: View {
    @EnvironmentObject private var app: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            GroupBox("我的状态") {
                VStack(alignment: .leading, spacing: 6) {
                    if let me = app.meCurrent {
                        Text("区域：\(me.area)（\(me.areaId)）")
                        Text("座位：\(me.seatNo)")
                        Text("状态：\(me.status)")
                        if !me.beginTime.isEmpty || !me.endTime.isEmpty {
                            Text("时间：\(me.beginTime) - \(me.endTime)")
                        }
                    } else {
                        Text("未加载（点下方“刷新”）")
                            .foregroundStyle(.secondary)
                    }
                }
                .font(.callout)

                HStack {
                    Button("刷新") { Task { await app.refreshMe() } }
                    Spacer()
                }
            }

            GroupBox("座位操作") {
                HStack {
                    Button("临时离开") { Task { await app.tempLeave() } }
                    Button("签到/回来") { Task { await app.signin() } }
                    Button("完全离开") { Task { await app.finishLeave() } }
                }
                Text("“完全离开”需要在【设置】里填 finishPath（或 .env 的 LCC_SPACE_FINISH_PATH）。")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }
}

