/*=============================================================================
<Scramble Ritual: 135ms to 319ms> (2026)
Sound Design & Sound Programming: Suyeon Cho

OSC Receive(in): /scramble/obj, id, 0, bits, "00000000", x, 0.65, y, 0.30, tilt, 0, tension, 0
OSC Send(out): /audio, "0", amp(Float 0~1), tension(Float 0~1)
=============================================================================*/


(
// < 1. 초기 변수 및 실행 토큰 설정 >
var sourcePath = thisProcess.nowExecutingPath;
var server = s;
var paths, runToken = UniqueID.next;

if (~stopScramble.notNil) { ~stopScramble.value; }; // 이전에 돌아가던 시스템함수(~stopScramble)가 있다면, 먼저 멈추고 정리
~scrambleRunToken = runToken;

// < 2. 환경 설정 파라미터 > - 네트워크, WAV, 사운드 파라미터(왜곡량)
~sampleFolder = "C:/Users/baksh/scramble_ritual/Sound/prototype_00/";
~sampleNames = nil;

~receivePort = 57120;
~targetIP = "192.168.0.7";
~targetPort = 9000;
~outputKey = \idString;
~timeout = 0.3;
~sendHz = 30;
~chaosLevel = 0.8;
~minimumBits = 6;
~resolveAt = 1.0;
~xIsZeroToOne = true;
~autoBoot = true;
~showGUI = false;
~debugOSC = false;
~audioSendEnabled = true;

// < 3. 파일 유효성 검사 >: WAV 경로와 25개 파일 구성 검토.
if (~sampleFolder.isNil) {
	if (sourcePath.isNil) { Error("먼저 .scd를 저장하거나 sampleFolder를 지정해야함.").throw; };
	~sampleFolder = sourcePath.dirname;
};
if (PathName(~sampleFolder).isFolder.not) { Error("sampleFolder 폴더가 없음.").throw; };


paths = if (~sampleNames.notNil) {
	~sampleNames.collect { |name| ~sampleFolder +/+ name }
} {
	PathName(~sampleFolder).files.select { |f| (f.extension ? "").toLower == "wav" }
	.collect(_.fullPath).sort
};
if (paths.size != 25) { Error("정확히 25개 WAV가 필요함. 발견: " ++ paths.size).throw; };
if (paths.asSet.size != 25) { Error("sampleNames에 중복 파일이 있음.").throw; };
// -> 지정 폴더에서 .wav 파일을 찾아 이름순으로 정렬. 파일 개수가 정확히 25개가 아니거나 중복 파일이 있으면 즉시 중단.

paths.do { |path, id|
	var f = SoundFile.new, valid;
	if (f.openRead(path).not) { Error("WAV를 열 수 없음: " ++ path).throw; };
	valid = (f.numChannels == 1) and: { f.numFrames > 0 };
	f.close;
	if (valid.not) { Error("비어 있지 않은 mono WAV가 필요함: " ++ path).throw; };
	("ID % -> %".format(id, PathName(path).fileName)).postln;
};
if (thisProcess.openUDPPort(~receivePort).not) { Error("OSC 수신 포트를 열 수 없음.").throw; };
// -> 25개 파일이 모두 정상적인 모노 사운드인지 헤더를 열어 검사하고, 각 파일에 0번부터 24번까지 ID 부여하기. 수신 포트(57120)를 활성화.


// < 4. 이전 상태 정리 및 전역 관리 딕셔너리 초기화 >
// 이전에 켜져 있던 루틴, OSC 수신기, 서버 그룹, 버퍼, 버스를 완전히 메모리에서 해제.
if (~scramble.notNil) { ~scramble[\stop].value; ~scramble = nil; };

// 이전 prototype 코드에서 전환할 때 중복 OSC 수신과 오래된 신스를 정리.
if (~cleanupRoutine.notNil) { ~cleanupRoutine.stop; ~cleanupRoutine = nil; };
[\receiveOsc, \receiveFromMax, \sendObjAmp].do { |name| OSCdef(name).free; };
if (server.serverRunning) {
	[~sourceGroup, ~fxGroup, ~masterGroup].do { |g| if(g.notNil) { g.free; }; };
	if (topEnvironment[\buffers].notNil) { topEnvironment[\buffers].do(_.free); };
	[~dryBus, ~revBus, ~rootPitchBus].do { |bus| if(bus.notNil) { bus.free; }; };
};



~sourceGroup = nil; ~fxGroup = nil; ~masterGroup = nil;
~dryBus = nil; ~revBus = nil; ~rootPitchBus = nil;
topEnvironment[\buffers] = nil;
OSCFunc.trace(false);
~scrambleEnabled = true;
~scrambleReady = false;
~scrambleBusy = false;
~activeBits = Dictionary.new;
// ~activeBits: 현재 화면에 살아있는 오브제 정보(신스 객체, 마지막 인식 시간 등)를 ID별로 보관
~nodeToObjectMap = Dictionary.new;
// ~nodeToObjectMap: 서버 신스 노드 번호(nodeID)로 오브제 정보를 즉시 찾기 위한 역방향 지도
~bitsByID = Array.fill(25, { |id| id.asBinaryString(8) });
~tensionByID = Array.fill(25, 0.0);
~lastOutputIDs = Set.new; // 마지막에 실제 송신을 시도한 오브제만 기록
~leaderID = nil;
~buffers = nil;
~resourceBuses = nil;
~systemGroup = nil;
~lastHeartbeat = 0.0;
~lastRetry = -100.0;
~badPackets = 0;
~lastWarning = -100.0;
~lastSendError = -100.0;
~nextSendTime = 0.0;
~remoteTarget = NetAddr(~targetIP, ~targetPort);
//~remoteTarget: 파이썬 수신 컴퓨터의 네트워크 목적지 주소

~isFiniteNumber = { |v| v.isNumber and: { v.asFloat.abs < inf } };
~scrambleWarn = { |message|
	if ((Process.elapsedTime - ~lastWarning) > 5) {
		message.warn; ~lastWarning = Process.elapsedTime;
	};
};


// < 5. 보조 유틸리티 함수들 >: 송신 오류를 처리하고, 연결 실패 후에는 5초 간격으로 재시도.
~sendAudio = { |id, amp, tension|
	var key, now = Process.elapsedTime, success = false;
	if (~audioSendEnabled and: { now >= ~nextSendTime }) {
		key = if (~outputKey == \bits) { ~bitsByID[id] } {
			if (~outputKey == \id) { id } { id.asString }
		};
		try {
			~remoteTarget.sendMsg('/audio', key, amp.asFloat, tension.asFloat);
			success = true;
		} { |err|
			~nextSendTime = now + 5.0;
			if (~debugOSC) {
				("OSC 송신 대상에 연결할 수 없음: " ++ err.errorString).warn;
			};
		};
	};
	success;
};


~sendFinalZeros = {
	// 실제 활동한 오브제에 대해서만 종료값을 한 번 보냄
	~lastOutputIDs.asArray.do { |id| ~sendAudio.value(id, 0.0, 0.0); };
	~lastOutputIDs.clear;
};

~selectLeader = {
	var leader = ~leaderID;
	if (leader.isNil or: { ~activeBits[leader].isNil }) {
		~leaderID = (0..24).detect { |id| ~activeBits[id].notNil };
	};
	if (~pitchSelectorSynth.notNil) {
		~pitchSelectorSynth.set(\index, ~leaderID ? 0, \active, if(~leaderID.isNil) { 0 } { 1 });
	};
}; // (*) 화음의 기준(기음)을 잡아줄 리더(기준) 오브제를 선출. 기존 리더가 퇴장하면 화면에 남아있는 다음 오브제에게 리더 권한을 자동으로 인계

~forgetNode = { |nodeID|
	var info = ~nodeToObjectMap.removeAt(nodeID);
	if (info.notNil) {
		if (~activeBits[info[\id]] === info) {
			~activeBits.removeAt(info[\id]);
			~selectLeader.value;
		};
	};
};

~releaseObject = { |id|
	var info = ~activeBits[id];
	if (info.notNil) {
		~activeBits.removeAt(id);
		info[\releasing] = true;
		info[\deadline] = Process.elapsedTime + 0.5;
		info[\synth].set(\gate, 0);
		~selectLeader.value;
	};
};


// < 6. 초기화 및 사운드 엔진 실행 (~startScramble) >
~freeScrambleResources = {
	~scrambleReady = false;
	if (server.serverRunning) {
		// 같은 로컬 서버로 노드 해제 명령을 버퍼 해제보다 먼저 전송.
		if (~systemGroup.notNil) { ~systemGroup.free; };
		if (~buffers.notNil) { ~buffers.do { |b| if(b.notNil) { b.free; }; }; };
		if (~resourceBuses.notNil) { ~resourceBuses.do { |bus| if(bus.index.notNil) { bus.free; }; }; };
	};
	~systemGroup = nil; ~buffers = nil; ~resourceBuses = nil;
	~sourceGroup = nil; ~pitchGroup = nil; ~fxGroup = nil; ~masterGroup = nil;
	~dryBus = nil; ~revBus = nil; ~voicePitchBus = nil; ~rootPitchBus = nil;
	~reverbSynth = nil;
	~pitchSelectorSynth = nil; ~masterSynth = nil;
	~activeBits = Dictionary.new; ~nodeToObjectMap.clear; ~leaderID = nil;
};
~stopScramble = {
	~scrambleEnabled = false;
	if (~initRoutine.notNil) { ~initRoutine.stop; };
	if (~cleanupRoutine.notNil) { ~cleanupRoutine.stop; };
	[\receiveOsc, \sendObjAmp, \scrambleHeartbeat, \scrambleNodeEnd, \scrambleServerFail].do { |name| OSCdef(name).free; };
	ServerBoot.remove(~scrambleOnBoot, server);
	ServerQuit.remove(~scrambleOnQuit, server);
	CmdPeriod.remove(~scrambleOnCmdPeriod);
	~freeScrambleResources.value; ~sendFinalZeros.value;
	~scrambleBusy = false;
	"[Scramble] STOP".postln;
};
~scrambleStatus = {
	("[Scramble] enabled:% ready:% active:% nodes:% invalidPackets:% CPU:%/%"
		.format(~scrambleEnabled, ~scrambleReady, ~activeBits.size,
			~nodeToObjectMap.size, ~badPackets, server.avgCPU, server.peakCPU)).postln;
};

~startScramble = {
	if (~scrambleEnabled and: { ~scrambleBusy.not } and: { server.serverRunning }) {
		~scrambleBusy = true; ~scrambleReady = false; ~lastRetry = Process.elapsedTime;
		~initStarted = Process.elapsedTime;
		~initRoutine = Routine({
			var initToken = UniqueID.next;
			~scrambleInitToken = initToken;
			try {
				// [1] 서버 준비 및 기존 자원 정리
				~freeScrambleResources.value;
				server.sync;
				if (~showGUI) { ~showGUI = false;
					server.meter; server.plotTree; server.freqscope; server.scope;
				};
				"=== [1/4] 서버 준비 완료 ===".postln;
				// [2] 25개 오디오 버퍼 로드
				~loadError = false;
				~loaded = 0;
				~buffers = Array.newClear(25);
				paths.do { |path, id|
					~buffers[id] = Buffer.read(server, path, action: { if (~scrambleRunToken == runToken and: { ~scrambleInitToken == initToken }) { ~loaded = ~loaded + 1; }; });
				}; // 25개의 사운드 파일을 서버 버퍼 메모리에 비동기로 업로드

				server.sync;

				while { (~loaded < 25) and: { ~loadError.not }
					and: { (Process.elapsedTime - ~initStarted) < 60 } } { 0.02.wait; };
				if (~loaded != 25) { Error("WAV 로드 완료 응답이 부족합니다.").throw; };
				if (~loadError or: { ~buffers.any { |b| b.numFrames <= 0 or: { b.numChannels != 1 } } }) {
					Error("버퍼/WAV 로드 실패. 서버 오류를 확인하세요.").throw;
				};
				"=== [2/4] 버퍼 로딩 완료 ===".postln;

				// [3] SynthDef 정의 (오브제 사운드 합성)
				SynthDef(\bufferPlayer, {
					arg dryOut = 0, revOut = 0, buf = 0, amp = 0.35, rate = 1, pan = 0.0, gate = 1,
					atk = 0.01, rel = 0.2, x = 0.0, y = 0.0, tilt = 0.0, tension = 0.0,
					pitchBus = 0, rootBus = 0, loopInterval = 1.0, id = 0,
					chaosLevel = 1.0, minimumBits = 6, resolved = 0;

					var sig, env, lagTension, isResolved, chaosAmt, chaosGainComp;
					var pitchChaos, noiseMod, fmCarrier, harshMod, foldedSig, crushSig;
					var chaosMix, harmonicSig, detectedPitch, hasFreq, baseFreq, chordFreqs, resArray, processedSig;
					var ampFollow, individualAmp, sendTrig;
					var basePan, stereoChaos, stereoHarmonic;
					var bufDur, trigRate, trig, playEnv;
					var sendToRev, sendToDry, tensionCurve;

					bufDur = BufDur.kr(buf) / rate.abs.max(0.001);
					trigRate = 1.0 / (bufDur + loopInterval.max(0.01));
					trig = Impulse.ar(trigRate);

					playEnv = EnvGen.ar(Env.linen(0.005, (bufDur - 0.02).max(0), 0.015), trig);

					lagTension = Lag.kr(tension.clip(0.0, 1.0), 0.03);
					isResolved = resolved.clip(0, 1);
					tensionCurve = lagTension.linlin(0.5, 0.99, 0, 1).clip(0, 1);
					chaosAmt = tensionCurve.squared * (1 - isResolved);

					chaosGainComp = (1 - (chaosAmt * 0.35));
					env = EnvGen.kr(Env.asr(atk, 1.0, rel), gate, doneAction: 2);

					pitchChaos = 1 + (LFNoise1.kr(lagTension.linlin(0, 1, 0, 8)).bipolar(0.12 * chaosAmt));

					sig = PlayBuf.ar(1, buf, BufRateScale.kr(buf) * rate * pitchChaos, trigger: trig, loop: 0);
					ampFollow = Amplitude.kr(sig, 0.01, 0.1);

					# detectedPitch, hasFreq = Pitch.kr(sig, initFreq: 220, minFreq: 60, maxFreq: 1000);
					detectedPitch = detectedPitch.clip(80, 880);
					detectedPitch = Gate.kr(detectedPitch, hasFreq);
					detectedPitch = Select.kr(detectedPitch > 50, [220, detectedPitch]);
					ReplaceOut.kr(pitchBus, detectedPitch);

					baseFreq = In.kr(rootBus, 1);
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
						bits: lagTension.linlin(0.0, 0.99, 24, minimumBits)
					);

					crushSig = (BPF.ar(crushSig, LFNoise1.kr(10).exprange(800, 5000), 0.3) * (chaosAmt * 0.8)) + crushSig;
					crushSig = crushSig.distort * 0.65;

					chaosMix = ((sig * (1 - chaosAmt)) + (crushSig * chaosAmt * chaosLevel)) * chaosGainComp;

					// B. Resolution
					chordFreqs = [baseFreq, baseFreq * 1.5, baseFreq * 2, baseFreq * 3, baseFreq * 4];

					resArray = DynKlank.ar(
						`[chordFreqs, [0.06, 0.05, 0.04, 0.02, 0.01], [0.6, 0.5, 0.4, 0.3, 0.2]],
						LPF.ar(sig, 1500)
					);

					harmonicSig = (resArray * 0.2) + (SinOsc.ar(chordFreqs[0..2]).sum * ampFollow * 0.05);

					// C. x 좌표에 따른 2채널 패닝
					basePan = Lag.kr(x.clip(-1.0, 1.0), 0.05);

					stereoChaos = Pan2.ar(chaosMix, basePan);
					stereoHarmonic = Pan2.ar(harmonicSig, basePan) * 0.4;

					processedSig = SelectX.ar(Lag.kr(isResolved, 0.08), [stereoChaos, stereoHarmonic]);
					processedSig = processedSig.tanh * env * playEnv * amp;

					individualAmp = Amplitude.kr(
						(processedSig[0].abs + processedSig[1].abs) * 2.5,
						attackTime: 0.01,
						releaseTime: 0.08
					).clip(0.0, 1.0);

					sendTrig = Impulse.kr(30); // 실시간 음량 측정 속도 및 서버에서 언어로 쏘아 올리는 속도(30=초당30회)
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
				}).send(server);

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
				}).send(server);


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
						relaxTime: 0.15
					);

					compressedSig = (compressedSig * 1.25).tanh;
					finalSig = Limiter.ar(LeakDC.ar(compressedSig), 0.90, dur: 0.01);

					Out.ar(out, finalSig);
					SendReply.kr(Impulse.kr(1), '/scramble/heartbeat', [1]);
				}).send(server);


				// 4. 기음 인계 선택기 - 현재 리더 오브제의 음정을 뽑아 메인 음정 버스로 복사. 교통정리 신스.
				SynthDef(\pitchSelectorEngine, { |inBus=0, outBus=0, index=0, active=0|
					var root = In.kr(inBus + index.clip(0, 24), 1);
					root = Select.kr((active > 0) * (root > 50), [220, root]);
					ReplaceOut.kr(outBus, root);
				}).send(server);

				server.sync;
				if (~loadError) { Error("SynthDef 등록 실패: sc3-plugins 설치 확인.").throw; };
				"=== [3/4] SynthDef 등록 완료 ===".postln;

				// [4] 버스 / 그룹 / 리버브 / 마스터 실행
				~resourceBuses = List.new;
				~dryBus = Bus.audio(server, 2); ~resourceBuses.add(~dryBus);
				~revBus = Bus.audio(server, 2); ~resourceBuses.add(~revBus);
				~voicePitchBus = Bus.control(server, 25); ~resourceBuses.add(~voicePitchBus);
				~rootPitchBus = Bus.control(server, 1); ~resourceBuses.add(~rootPitchBus);
				if (~resourceBuses.any { |bus| bus.index.isNil }) { Error("버스 할당 실패.").throw; };
				~voicePitchBus.setn(Array.fill(25, 220)); ~rootPitchBus.set(220);
				~systemGroup = Group.tail(server.defaultGroup);
				~sourceGroup = Group.head(~systemGroup);
				~pitchGroup = Group.after(~sourceGroup);
				~fxGroup = Group.after(~pitchGroup);
				~masterGroup = Group.after(~fxGroup);

				~pitchSelectorSynth = Synth(\pitchSelectorEngine,
					[\inBus, ~voicePitchBus, \outBus, ~rootPitchBus], ~pitchGroup);
				~reverbSynth = Synth(\reverbEngine,
					[\inBus, ~revBus, \outBus, ~dryBus, \rt60, 3.5], ~fxGroup);
				~masterSynth = Synth(\masterOutputEngine,
					[\dryBus, ~dryBus, \revBus, ~revBus, \out, 0], ~masterGroup);
				server.sync;
				if (~loadError) { Error("오디오 그래프 생성 실패.").throw; };
				~lastHeartbeat = Process.elapsedTime; ~graphFailed = false;
				~scrambleReady = true; ~scrambleBusy = false;
				"=== [4/4] 시스템 준비 완료 — ID 0~24 ===".postln;
				("OSC IN:% OUT:%:% /audio key:%".format(~receivePort,
					~targetIP, ~targetPort, ~outputKey)).postln;
			} { |err|
				~scrambleBusy = false; ~initRoutine = nil;
				~stopScramble.value;
				err.reportError;
			};
		}).play(AppClock);
	};
};

OSCdef(\scrambleServerFail, { |msg|
	if (~scrambleBusy) { ~loadError = true; };
	~scrambleWarn.value("서버 /fail: " ++ msg.asString);
}, '/fail', server.addr);
OSCdef(\scrambleHeartbeat, { |msg|
	if (~masterSynth.notNil and: { msg[1] == ~masterSynth.nodeID }) {
		~lastHeartbeat = Process.elapsedTime;
	};
}, '/scramble/heartbeat', server.addr);
OSCdef(\scrambleNodeEnd, { |msg|
	~forgetNode.value(msg[1]);
	if (~scrambleReady and: {
		[~systemGroup, ~pitchSelectorSynth, ~reverbSynth, ~masterSynth].any { |node|
			node.notNil and: { node.nodeID == msg[1] }
		}
	}) { ~graphFailed = true; };
}, '/n_end', server.addr);


// 7. OSC 이벤트 리스너 - 서버 신스가 측정한 개별 볼륨을 받아서 해당 오브제 메모리에 갱신
OSCdef(\sendObjAmp, { |msg|
	var info = ~nodeToObjectMap[msg[1]];
	if (info.notNil and: { msg.size >= 5 } and: { ~isFiniteNumber.value(msg[4]) }) {
		if (msg[3].asInteger == info[\id]) {
			info[\amp] = msg[4].asFloat.clip(0, 1);
			info[\replyTime] = Process.elapsedTime;
		};
	};
}, '/objAmpData', server.addr);


// 카메라 트래킹 프로그램에서 /scramble/obj 메시지가 들어올 때 호출됨
// 좌표(x, y, tilt)와 tension 값 파싱
OSCdef(\receiveOsc, { |msg|
	var data = Dictionary.new, valid = true, id, x, y, tilt, tension, bits, info, now;
	if (~scrambleEnabled and: { ~scrambleReady }) {
		valid = (msg.size > 1) and: { ((msg.size - 1) % 2) == 0 };
		if (valid) {
			((msg.size - 1).div(2)).do { |i|
				var key = msg[1 + (i * 2)].asString;
				if (data.includesKey(key)) { valid = false; };
				data[key] = msg[2 + (i * 2)];
			};
			valid = valid and: { ["id", "x", "y", "tilt", "tension"].every { |k| ~isFiniteNumber.value(data[k]) } };
		};
		if (valid) {
			id = data["id"];
			valid = (id >= 0) and: { id <= 24 } and: { id == id.asInteger };
		};
		if (valid) {
			id = id.asInteger;
			x = data["x"].asFloat;
			if (~xIsZeroToOne) { x = x.linlin(0, 1, -1, 1); };
			x = x.clip(-1, 1); y = data["y"].asFloat.clip(-1, 1);
			tilt = data["tilt"].asFloat.clip(-1, 1);
			tension = data["tension"].asFloat.clip(0, 1);
			bits = data["bits"];
			if (bits.notNil) { ~bitsByID[id] = bits.asString; };
			~tensionByID[id] = tension;
			now = Process.elapsedTime;
			info = ~activeBits[id];
			if (info.isNil) {
				var dur = ~buffers[id].duration, interval, amp, synth;
				~nodeToObjectMap.keys.asArray.do { |nodeID|
					var tail = ~nodeToObjectMap[nodeID];
					if (tail[\id] == id) { tail[\synth].free; ~forgetNode.value(nodeID); };
				};
				if (dur >= 8.0) {
					interval = [4.5, 5.5, 6.5, 7.5].wrapAt(id); amp = 0.28;
				} {
					interval = [0.7, 1.1, 1.4, 1.8, 2.3, 2.7, 3.1].wrapAt(id); amp = 0.35;
				};
				synth = Synth.tail(~sourceGroup, \bufferPlayer, [
					\dryOut, ~dryBus, \revOut, ~revBus, \buf, ~buffers[id],
					\amp, amp, \x, x, \y, y, \tilt, tilt, \tension, tension,
					\resolved, if(tension >= ~resolveAt) { 1 } { 0 },
					\pitchBus, ~voicePitchBus.index + id, \rootBus, ~rootPitchBus,
					\loopInterval, interval, \id, id, \gate, 1,
					\chaosLevel, ~chaosLevel, \minimumBits, ~minimumBits
				]);
				info = (id: id, synth: synth, lastTime: now, replyTime: now,
					amp: 0.0, releasing: false, deadline: 0.0);
				~activeBits[id] = info; ~nodeToObjectMap[synth.nodeID] = info;
				~selectLeader.value;
			} {
				info[\lastTime] = now;
				info[\synth].set(\x, x, \y, y, \tilt, tilt, \tension, tension,
					\resolved, if(tension >= ~resolveAt) { 1 } { 0 });
			};
		} {
			~badPackets = ~badPackets + 1;
			~scrambleWarn.value("무효 OSC 무시: id(0~24), x, y, tilt, tension의 숫자 형식을 확인하세요.");
		};
	};
}, '/scramble/obj', recvPort: ~receivePort);

~scrambleOnQuit = {
	if (~scrambleEnabled) {
		if (~initRoutine.notNil) { ~initRoutine.stop; };
		~scrambleBusy = false; ~scrambleReady = false;
		~systemGroup = nil; ~buffers = nil; ~resourceBuses = nil;
		~sourceGroup = nil; ~pitchGroup = nil; ~fxGroup = nil; ~masterGroup = nil;
		~dryBus = nil; ~revBus = nil; ~voicePitchBus = nil; ~rootPitchBus = nil;
		~reverbSynth = nil;
		~pitchSelectorSynth = nil; ~masterSynth = nil;
		~activeBits = Dictionary.new; ~nodeToObjectMap.clear; ~leaderID = nil;
		~sendFinalZeros.value;
		"[Scramble] 서버 종료 감지 — 재부팅 대기".warn;
	};
};
~scrambleOnBoot = {
	if (~scrambleRunToken == runToken and: { ~scrambleEnabled }) {
		{ if (~scrambleRunToken == runToken and: { ~scrambleEnabled }) {
			~startScramble.value;
		}; }.defer(0.5);
	};
};
~scrambleOnCmdPeriod = { ~stopScramble.value; };
ServerBoot.add(~scrambleOnBoot, server);
ServerQuit.add(~scrambleOnQuit, server);
CmdPeriod.add(~scrambleOnCmdPeriod);


// 8. 백그라운드 관리 루틴 - 시스템의 메인 관제탑. 1초에 30번씩 돌면서 퇴장 감지, 상태 감시, 데이터 전송 수행
~cleanupRoutine = Routine({
	loop {
		var now = Process.elapsedTime, amps = Array.fill(25, 0.0), present = Array.fill(25, false);
		if (~scrambleEnabled) {
			if (~scrambleReady and: { server.serverRunning }) {
				25.do { |id|
					var info = ~activeBits[id];
					if (info.notNil and: { (now - info[\lastTime]) > ~timeout }) {
						~releaseObject.value(id);
					};
				};
				~nodeToObjectMap.keys.asArray.do { |nodeID|
					var info = ~nodeToObjectMap[nodeID];
					if ((info[\releasing] and: { now > info[\deadline] })
						or: { (now - info[\replyTime]) > 2.0 }) {
						info[\synth].free; ~forgetNode.value(nodeID);
					} {
						amps[info[\id]] = amps[info[\id]] + info[\amp];
						present[info[\id]] = true;
					};
				};
				if ((~graphFailed or: { (now - ~lastHeartbeat) > 5 }) and: { (now - ~lastRetry) > 10 }) {
					"[Scramble] master 응답 없음 — 그래프 재구성".warn;
					~startScramble.value;
				};
			};
			if (~scrambleBusy and: { (now - ~initStarted) > 65 }) {
				~scrambleEnabled = false;
				{ if (~scrambleRunToken == runToken) {
					"[Scramble] 초기화 시간 초과 — 중지 후 설정 확인.".warn;
					~stopScramble.value;
				}; }.defer;
			};
			25.do { |id|
				if (present[id]) {
					~lastOutputIDs.add(id);
					~sendAudio.value(id, amps[id].clip(0, 1), ~tensionByID[id]);
				} {
					if (~lastOutputIDs.includes(id)) {
						~sendAudio.value(id, 0.0, 0.0);
						~lastOutputIDs.remove(id);
					};
				};
			};
			if (~autoBoot and: { server.serverRunning.not } and: { server.serverBooting.not }
				and: { (now - ~lastRetry) > 10 }) {
				~lastRetry = now; server.boot;
			};
		};
		(1.0 / ~sendHz).wait;
	};
}).play(SystemClock);

if (server.serverRunning) { ~startScramble.value; } {
	if (~autoBoot) { ~lastRetry = Process.elapsedTime; server.boot; };
};
)
