/*
=============================================================================
<Scramble Ritual: 135ms to 319ms> (2026)
Sound Design & Programming: Suyeon Cho

Requirements & Dependencies:
- SuperCollider 3.13+
- sc3-plugins (Required UGens: Decimator, NHHall)

I/O Specifications:
- Input:  OSC '/scramble/obj' [bitStr, x, y, tilt, tension] (Port: Default SC port)
- Output: OSC '/audio' [bitStr, amp, tension] -> Ferrofluid (Port 9000)
=============================================================================
*/

(
s.waitForBoot({
    var folderPath, filePaths;

    // [1] 모니터링 GUI 실행
    s.meter;
    s.plotTree;
    s.freqscope;
    s.scope;

    "=== [1/4] SuperCollider Server 부팅 완료 ===".postln;
    ("Client 수신 포트: " ++ NetAddr.localAddr.port).postln;

    // [2] 25개 오디오 버퍼 동적 로드
    folderPath = thisProcess.nowExecutingPath.dirname +/+ "";

    if (topEnvironment[\buffers].notNil) {
        topEnvironment[\buffers].do(_.free);
        topEnvironment[\buffers] = nil;
    };

    filePaths = PathName(folderPath).files
        .select { |file| file.extension.toLower == "wav" }
        .collect { |file| file.fullPath }
        .sort;

    ("총 " ++ filePaths.size ++ "개의 오디오 파일 로드").postln;

    topEnvironment[\buffers] = filePaths.collect { |path|
        Buffer.read(s, path);
    };

    s.sync; // 모든 버퍼가 서버 메모리에 완전히 올라갈 때까지 대기
    "=== [2/4] 오디오 버퍼 로딩 완료 ===".postln;

    // [3] SynthDef 정의 및 서버 등록
    // 1. 개별 소스 신스
    SynthDef(\bufferPlayer, {
        arg dryOut = 0, revOut = 0, buf = 0, amp = 0.35, rate = 1, pan = 0.0, gate = 1,
            atk = 0.01, rel = 0.2, x = 0.0, y = 0.0, tilt = 0.0, tension = 0.0,
            pitchBus = 0, isFirst = 0, loopInterval = 1.0, id = 0;

        var sig, env, lagTension, isResolved, chaosAmt, chaosGainComp;
        var pitchChaos, noiseMod, fmCarrier, harshMod, foldedSig, crushSig;
        var chaosMix, harmonicSig, detectedPitch, hasFreq, baseFreq, chordFreqs, resArray, processedSig;
        var ampFollow, individualAmp, sendTrig;
        var basePan, panJitter, dynamicPan, stereoChaos, stereoHarmonic;
        var bufDur, trigRate, trig, playEnv;
        var sendToRev, sendToDry;

        bufDur = BufDur.kr(buf) / rate.abs.max(0.001);
        trigRate = 1.0 / (bufDur + loopInterval.max(0.01));
        trig = Impulse.ar(trigRate);

        playEnv = EnvGen.ar(Env.linen(0.005, bufDur - 0.02, 0.015), trig);

        lagTension = Lag.kr(tension.clip(0.0, 1.0), 0.03);
        isResolved = (lagTension >= 0.99);
        chaosAmt = (lagTension ** 2.0) * (1 - isResolved);

        chaosGainComp = (1 - (chaosAmt * 0.35));
        env = EnvGen.kr(Env.asr(atk, 1.0, rel), gate, doneAction: 2);

        pitchChaos = 1 + (LFNoise1.kr(lagTension.linlin(0, 1, 0, 8)).bipolar(0.12 * chaosAmt));
        sig = PlayBuf.ar(1, buf, BufRateScale.kr(buf) * rate * pitchChaos, trigger: trig, loop: 0);
        ampFollow = Amplitude.kr(sig, 0.01, 0.1);

        # detectedPitch, hasFreq = Pitch.kr(sig, initFreq: 220, minFreq: 60, maxFreq: 1000);
        detectedPitch = detectedPitch.clip(80, 880);
        Out.kr(pitchBus, detectedPitch * isFirst);

        baseFreq = In.kr(pitchBus, 1);
        baseFreq = Select.kr(baseFreq > 50, [detectedPitch, baseFreq]);

        // A. Chaos
        noiseMod = LFNoise0.ar(chaosAmt.linexp(0.0001, 1, 40, 4000)) * (chaosAmt * 8.0);
        fmCarrier = SinOsc.ar(LFNoise2.kr(12).exprange(50, 2400) + (noiseMod * 200));

        harshMod = sig * fmCarrier * (chaosAmt * 4.0);
        foldedSig = (sig + harshMod) * (1 + (chaosAmt * 4.0));
        foldedSig = foldedSig.fold2(0.5);

        crushSig = Decimator.ar(
            foldedSig,
            rate: lagTension.linexp(0.0001, 0.99, 44100, 1800),
            bits: lagTension.linlin(0.0, 0.99, 24, 4)
        );

        crushSig = (BPF.ar(crushSig, LFNoise1.kr(10).exprange(800, 5000), 0.3) * (chaosAmt * 0.8)) + crushSig;
        crushSig = crushSig.distort * 0.65;

        chaosMix = ((sig * (1 - chaosAmt)) + (crushSig * chaosAmt * 1.5)) * chaosGainComp;

        // B. Resolution
        chordFreqs = [baseFreq, baseFreq * 1.5, baseFreq * 2, baseFreq * 3, baseFreq * 4];

        resArray = DynKlank.ar(
            `[chordFreqs, [0.06, 0.05, 0.04, 0.02, 0.01], [0.6, 0.5, 0.4, 0.3, 0.2]],
            LPF.ar(sig, 1800)
        );

        harmonicSig = (resArray * 0.2) + (SinOsc.ar(chordFreqs[0..2]).sum * ampFollow * 0.05);

        // C. 동적 패닝 및 결합
        basePan = Lag.kr((x + (tilt * 0.3)).clip(-1.0, 1.0), 0.05);
        panJitter = LFNoise1.kr(chaosAmt.linexp(0.0001, 1, 0.5, 18)).bipolar(chaosAmt * 0.85);
        dynamicPan = (basePan + panJitter).clip(-1.0, 1.0);

        stereoChaos = Pan2.ar(chaosMix, dynamicPan);
        stereoHarmonic = [
            Pan2.ar(harmonicSig, (basePan - 0.35).clip(-1.0, 1.0))[0],
            Pan2.ar(harmonicSig, (basePan + 0.35).clip(-1.0, 1.0))[1]
        ] * 0.4;

        processedSig = SelectX.ar(Lag.kr(isResolved, 0.08), [stereoChaos, stereoHarmonic]);
        processedSig = processedSig.tanh * env * playEnv * amp;

        individualAmp = Amplitude.kr(
            (processedSig[0].abs + processedSig[1].abs) * 2.5,
            attackTime: 0.01,
            releaseTime: 0.08
        ).clip(0.0, 1.0);

        sendTrig = Impulse.kr(30);
        SendReply.kr(sendTrig, '/objAmpData', [id, individualAmp]);

        sendToRev = SelectX.kr(isResolved, [
            lagTension.linlin(0.0, 0.99, 0.0, 0.08),
            0.50
        ]);

        sendToDry = SelectX.kr(isResolved, [
            1.0 - sendToRev,
            0.50
        ]);

        Out.ar(dryOut, processedSig * sendToDry);
        Out.ar(revOut, processedSig * sendToRev);
    }).add;

    // 2. 리버브 엔진
    SynthDef(\reverbEngine, {
        arg inBus = 0, outBus = 0, rt60 = 3.5, stereo = 0.75;
        var input, wetSig;

        input = In.ar(inBus, 2);

        wetSig = NHHall.ar(
            input,
            rt60: rt60,
            stereo: stereo,
            lowFreq: 250,
            lowRatio: 0.8,
            hiFreq: 5000,
            hiRatio: 0.6,
            earlyDiffusion: 0.7,
            lateDiffusion: 0.8,
            modRate: 0.2,
            modDepth: 0.3
        );

        Out.ar(outBus, wetSig);
    }).add;

    // 3. 최종 마스터 엔진
    SynthDef(\masterOutputEngine, {
        arg dryBus = 0, revBus = 0, out = 0;
        var drySig, revSig, mixedSig, compressedSig, finalSig;

        drySig = In.ar(dryBus, 2);
        revSig = In.ar(revBus, 2);

        mixedSig = drySig + revSig;

        compressedSig = Compander.ar(
            mixedSig, mixedSig,
            thresh: 0.28,
            slopeBelow: 0.75,
            slopeAbove: 0.3,
            clampTime: 0.005,
            relaxTime: 0.08
        );

        compressedSig = (compressedSig * 1.25).tanh;
        finalSig = Limiter.ar(LeakDC.ar(compressedSig), 0.90, dur: 0.01);

        Out.ar(out, finalSig);
    }).add;

    s.sync; // SynthDef 컴파일 및 서버 등록 완료 대기
    "=== [3/4] SynthDef 등록 완료 ===".postln;

    // [4] 실행 그룹 설정 및 OSC 라우팅 파이프라인
    ~targetIP = "192.168.0.20";
    ~targetPort = 9000;
    ~remoteTarget = NetAddr(~targetIP, ~targetPort);

    if (~cleanupRoutine.notNil) { ~cleanupRoutine.stop; ~cleanupRoutine = nil; };
    OSCdef(\receiveFromMax).free;
    OSCdef(\sendObjAmp).free;

    ~activeBits = Dictionary.new;
    ~idToBitMap = Dictionary.new;

    s.defaultGroup.freeAll;
    s.sync;

    if (~dryBus.notNil)       { ~dryBus.free;       ~dryBus = nil; };
    if (~revBus.notNil)       { ~revBus.free;       ~revBus = nil; };
    if (~rootPitchBus.notNil) { ~rootPitchBus.free; ~rootPitchBus = nil; };
    s.sync;

    ~dryBus = Bus.audio(s, 2);
    ~revBus = Bus.audio(s, 2);
    ~rootPitchBus = Bus.control(s, 1);
    ~rootPitchBus.set(220);

    ~sourceGroup = Group.new(s.defaultGroup, \addToHead);
    ~fxGroup = Group.after(~sourceGroup);
    ~masterGroup = Group.after(~fxGroup);
    s.sync;

    ~reverbSynth = Synth(\reverbEngine, [\inBus, ~revBus, \outBus, ~dryBus, \rt60, 3.5], ~fxGroup);
    ~masterSynth = Synth(\masterOutputEngine, [\dryBus, ~dryBus, \revBus, ~revBus, \out, 0], ~masterGroup);

    ~timeout = 0.3;

    // OSC 송신 (toFerro)
    OSCdef(\sendObjAmp, { |msg|
        var objId   = msg[3].asInteger;
        var rawAmp  = msg[4].asFloat;
        var bitStr  = ~idToBitMap[objId];
        var objInfo, currentTension, cleanAmp, cleanTension;

        if (bitStr.notNil) {
            objInfo = ~activeBits[bitStr];

            currentTension = if (objInfo.notNil and: { objInfo[\tension].notNil }) {
                objInfo[\tension].asFloat
            } {
                0.0
            };

            if (rawAmp < 0.005) {
                cleanAmp = 0.0;
            } {
                cleanAmp = rawAmp.round(0.0001);
            };

            cleanTension = currentTension.round(0.0001);

            try {
                ~remoteTarget.sendMsg('/audio', bitStr.asString, cleanAmp.asFloat, cleanTension.asFloat);

                if (cleanAmp > 0.01) {
					("[toFerro] Bit: % | Amp: % | Tension: %")
					.format(bitStr, cleanAmp, cleanTension)
					.postln;
                };
            } { |err| };
        };
    }, '/objAmpData', s.addr);

    // 타임아웃 클린업 루틴
    ~cleanupRoutine = Routine({
        loop {
            var now = Process.elapsedTime;

            ~activeBits.keysValuesDo({ |bitKey, info|
                if (now - info[\lastTime] > ~timeout) {
                    if (info[\synth].notNil) {
                        info[\synth].set(\gate, 0);
                    };
                    ~idToBitMap.removeAt(info[\id]);
                    ~activeBits.removeAt(bitKey);
                    ("[exitObj] Bit: % | 남은 개수: %").format(bitKey, ~activeBits.size).postln;
                };
            });

            if (~activeBits.size == 0) {
                ~rootPitchBus.set(220);
            };

            0.05.wait;
        }
    }).play;

    // OSC 수신 디펜더
    OSCdef(\receiveFromMax, { |msg|
        var bitStr  = msg[2].asString;
        var x       = if(msg[4].notNil)  { msg[4].asFloat }  { 0.0 };
        var y       = if(msg[6].notNil)  { msg[6].asFloat }  { 0.0 };
        var tilt    = if(msg[10].notNil)  { msg[10].asFloat }  { 0.0 };
        var tension = if(msg[12].notNil) { msg[12].asFloat } { 0.0 };

        var bufs = topEnvironment[\buffers];
        var currentObj = ~activeBits[bitStr];
        var rawDec, bufIndex, selectedBuf, isFirstObj;
        var sampleDur, chosenInterval, chosenAmp;

        if (bufs.notNil and: { bufs.size > 0 }) {
            if (currentObj.isNil) {
                isFirstObj = if (~activeBits.size == 0) { 1 } { 0 };

                if (bitStr.every({ |c| c == $0 or: { c == $1 } })) {
                    rawDec = ("2r" ++ bitStr).interpret;
                } {
                    rawDec = bitStr.asInteger;
                };

                bufIndex = rawDec % bufs.size;
                selectedBuf = bufs[bufIndex];
                sampleDur = selectedBuf.duration;

                if (sampleDur >= 8.0) {
                    chosenInterval = [4.5, 5.5, 6.5, 7.5].wrapAt(bufIndex);
                    chosenAmp = 0.28;
                } {
                    chosenInterval = [0.7, 1.1, 1.4, 1.8, 2.3, 2.7, 3.1].wrapAt(bufIndex);
                    chosenAmp = 0.35;
                };

                ~idToBitMap[rawDec] = bitStr;

                ~activeBits[bitStr] = (
                    \synth: Synth(\bufferPlayer, [
                        \dryOut, ~dryBus,
                        \revOut, ~revBus,
                        \buf, selectedBuf,
                        \amp, chosenAmp,
                        \pan, x.clip(-1.0, 1.0),
                        \x, x,
                        \y, y,
                        \tilt, tilt,
                        \tension, tension,
                        \pitchBus, ~rootPitchBus,
                        \isFirst, isFirstObj,
                        \loopInterval, chosenInterval,
                        \id, rawDec,
                        \gate, 1
                    ], ~sourceGroup),
                    \lastTime: Process.elapsedTime,
                    \tension: tension,
                    \isFirst: isFirstObj,
                    \id: rawDec
                );

                "[newObj] Bit: % (ID: %) | Duration: %s | Tension: %"
                .format(bitStr, rawDec, sampleDur.round(0.1), tension.round(0.01))
                .postln;

            } {
                currentObj[\lastTime] = Process.elapsedTime;
                currentObj[\tension] = tension;

                if (currentObj[\synth].notNil) {
                    currentObj[\synth].set(
                        \pan, x.clip(-1.0, 1.0),
                        \x, x,
                        \y, y,
                        \tilt, tilt,
                        \tension, tension
                    );
                };
            };
        };
    }, '/scramble/obj');

    "=== [4/4] 시스템 준비 완료 ===".postln;
    ("OSC전송대상(toFerro): " ++ ~targetIP ++ ":" ++ ~targetPort).postln;
});
)
