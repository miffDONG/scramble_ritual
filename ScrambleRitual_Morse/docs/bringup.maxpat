{
	"patcher" : {
		"fileversion" : 1,
		"appversion" : { "major" : 8, "minor" : 5, "revision" : 0 },
		"rect" : [ 80.0, 80.0, 740.0, 560.0 ],
		"boxes" : [
			{ "box" : { "id" : "comment-title", "maxclass" : "comment", "numinlets" : 1, "numoutlets" : 0, "patching_rect" : [ 20.0, 14.0, 480.0, 22.0 ], "text" : "Scramble Ritual — bringup OSC receiver (stages 4-5)", "fontsize" : 13.0 } },
			{ "box" : { "id" : "comment-help", "maxclass" : "comment", "numinlets" : 1, "numoutlets" : 0, "patching_rect" : [ 20.0, 36.0, 560.0, 22.0 ], "text" : "tracker.bringup --stage 4/5 --osc 127.0.0.1:9000 (+ --sound off so Max makes the sound)" } },
			{ "box" : { "id" : "udp", "maxclass" : "newobj", "numinlets" : 1, "numoutlets" : 1, "outlettype" : [ "" ], "patching_rect" : [ 20.0, 70.0, 120.0, 22.0 ], "text" : "udpreceive 9000" } },
			{ "box" : { "id" : "printraw", "maxclass" : "newobj", "numinlets" : 1, "numoutlets" : 0, "patching_rect" : [ 360.0, 70.0, 90.0, 22.0 ], "text" : "print SR" } },
			{ "box" : { "id" : "route", "maxclass" : "newobj", "numinlets" : 1, "numoutlets" : 6, "outlettype" : [ "", "", "", "", "", "" ], "patching_rect" : [ 20.0, 110.0, 320.0, 22.0 ], "text" : "route /sr/n /sr/obj /sr/dist /sr/nearest /sr/sound" } },
			{ "box" : { "id" : "comment-n", "maxclass" : "comment", "numinlets" : 1, "numoutlets" : 0, "patching_rect" : [ 20.0, 146.0, 70.0, 20.0 ], "text" : "n (count)" } },
			{ "box" : { "id" : "num-n", "maxclass" : "number", "numinlets" : 1, "numoutlets" : 2, "outlettype" : [ "", "bang" ], "patching_rect" : [ 20.0, 168.0, 50.0, 22.0 ] } },
			{ "box" : { "id" : "comment-near", "maxclass" : "comment", "numinlets" : 1, "numoutlets" : 0, "patching_rect" : [ 120.0, 146.0, 200.0, 20.0 ], "text" : "nearest: id_a id_b dist_norm" } },
			{ "box" : { "id" : "unpack-near", "maxclass" : "newobj", "numinlets" : 1, "numoutlets" : 3, "outlettype" : [ "int", "int", "float" ], "patching_rect" : [ 120.0, 168.0, 110.0, 22.0 ], "text" : "unpack 0 0 0." } },
			{ "box" : { "id" : "num-na", "maxclass" : "number", "numinlets" : 1, "numoutlets" : 2, "outlettype" : [ "", "bang" ], "patching_rect" : [ 120.0, 198.0, 40.0, 22.0 ] } },
			{ "box" : { "id" : "num-nb", "maxclass" : "number", "numinlets" : 1, "numoutlets" : 2, "outlettype" : [ "", "bang" ], "patching_rect" : [ 165.0, 198.0, 40.0, 22.0 ] } },
			{ "box" : { "id" : "num-nd", "maxclass" : "flonum", "numinlets" : 1, "numoutlets" : 2, "outlettype" : [ "", "bang" ], "patching_rect" : [ 210.0, 198.0, 60.0, 22.0 ] } },
			{ "box" : { "id" : "comment-snd", "maxclass" : "comment", "numinlets" : 1, "numoutlets" : 0, "patching_rect" : [ 360.0, 146.0, 320.0, 20.0 ], "text" : "sound 0..1 (close=1) -> pitch test tone" } },
			{ "box" : { "id" : "flo-snd", "maxclass" : "flonum", "numinlets" : 1, "numoutlets" : 2, "outlettype" : [ "", "bang" ], "patching_rect" : [ 360.0, 168.0, 60.0, 22.0 ] } },
			{ "box" : { "id" : "scale", "maxclass" : "newobj", "numinlets" : 6, "numoutlets" : 1, "outlettype" : [ "float" ], "patching_rect" : [ 360.0, 198.0, 180.0, 22.0 ], "text" : "scale 0. 1. 200. 1200." } },
			{ "box" : { "id" : "cycle", "maxclass" : "newobj", "numinlets" : 2, "numoutlets" : 1, "outlettype" : [ "signal" ], "patching_rect" : [ 360.0, 238.0, 80.0, 22.0 ], "text" : "cycle~" } },
			{ "box" : { "id" : "gain", "maxclass" : "newobj", "numinlets" : 2, "numoutlets" : 1, "outlettype" : [ "signal" ], "patching_rect" : [ 360.0, 278.0, 80.0, 22.0 ], "text" : "*~ 0.08" } },
			{ "box" : { "id" : "dac", "maxclass" : "newobj", "numinlets" : 2, "numoutlets" : 0, "patching_rect" : [ 360.0, 318.0, 80.0, 22.0 ], "text" : "ezdac~" } }
		],
		"lines" : [
			{ "patchline" : { "source" : [ "udp", 0 ], "destination" : [ "route", 0 ] } },
			{ "patchline" : { "source" : [ "udp", 0 ], "destination" : [ "printraw", 0 ] } },
			{ "patchline" : { "source" : [ "route", 0 ], "destination" : [ "num-n", 0 ] } },
			{ "patchline" : { "source" : [ "route", 3 ], "destination" : [ "unpack-near", 0 ] } },
			{ "patchline" : { "source" : [ "unpack-near", 0 ], "destination" : [ "num-na", 0 ] } },
			{ "patchline" : { "source" : [ "unpack-near", 1 ], "destination" : [ "num-nb", 0 ] } },
			{ "patchline" : { "source" : [ "unpack-near", 2 ], "destination" : [ "num-nd", 0 ] } },
			{ "patchline" : { "source" : [ "route", 4 ], "destination" : [ "flo-snd", 0 ] } },
			{ "patchline" : { "source" : [ "flo-snd", 0 ], "destination" : [ "scale", 0 ] } },
			{ "patchline" : { "source" : [ "scale", 0 ], "destination" : [ "cycle", 0 ] } },
			{ "patchline" : { "source" : [ "cycle", 0 ], "destination" : [ "gain", 0 ] } },
			{ "patchline" : { "source" : [ "gain", 0 ], "destination" : [ "dac", 0 ] } },
			{ "patchline" : { "source" : [ "gain", 0 ], "destination" : [ "dac", 1 ] } }
		]
	}
}
