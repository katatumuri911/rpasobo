// test/test_prompt_toolbar.cjs
// Unit tests for PromptToolbar core algorithms and state management.
const assert = require("assert");
const path = require("path");

const PromptToolbar = require(path.join(__dirname, "..", "public", "prompt_toolbar.js"));

console.log("Running PromptToolbar unit tests...");

// 1. Tag Normalization
assert.strictEqual(PromptToolbar.normalizeTagKey("  (pov:1.2) "), "pov");
assert.strictEqual(PromptToolbar.normalizeTagKey("[[[from behind]]]"), "from behind");
assert.strictEqual(PromptToolbar.normalizeTagKey("<lora:something:0.8>"), "<lora:something:0.8>");
assert.strictEqual(PromptToolbar.normalizeTagKey("{full body:0.9}"), "full body");

// 2. Count Tag Detection
assert.strictEqual(PromptToolbar.isCountTagKey("1girl"), true);
assert.strictEqual(PromptToolbar.isCountTagKey("2girls"), true);
assert.strictEqual(PromptToolbar.isCountTagKey("3boys"), true);
assert.strictEqual(PromptToolbar.isCountTagKey("solo"), true);
assert.strictEqual(PromptToolbar.isCountTagKey("pov"), false);
assert.strictEqual(PromptToolbar.isCountTagKey("from behind"), false);

// 3. parsePromptTags & joinPromptTags
assert.deepStrictEqual(
  PromptToolbar.parsePromptTags("1girl, from behind, full body\nbedroom"),
  ["1girl", "from behind", "full body", "bedroom"]
);
assert.strictEqual(
  PromptToolbar.joinPromptTags(["1girl", "from behind", "1girl", "bedroom"]),
  "1girl, from behind, bedroom"
);

// 4. POV toggle
assert.strictEqual(PromptToolbar.hasPovTag("1girl, pov, from behind"), true);
assert.strictEqual(PromptToolbar.hasPovTag("1girl, from behind"), false);
assert.strictEqual(PromptToolbar.hasPovTag("1girl, (pov:1.1), from behind"), true);

// Remove POV
assert.strictEqual(
  PromptToolbar.setPovInPrompt("1girl, pov, from behind", false),
  "1girl, from behind"
);
assert.strictEqual(
  PromptToolbar.setPovInPrompt("1girl, (pov:1.2), from behind", false),
  "1girl, from behind"
);

// Add POV after count tag
assert.strictEqual(
  PromptToolbar.setPovInPrompt("1girl, from behind", true),
  "1girl, pov, from behind"
);
assert.strictEqual(
  PromptToolbar.setPovInPrompt("1girl, 1boy, sitting", true),
  "1girl, 1boy, pov, sitting"
);

// Add POV after quality tag if no count tag
assert.strictEqual(
  PromptToolbar.setPovInPrompt("masterpiece, best quality, outdoors", true),
  "masterpiece, best quality, pov, outdoors"
);

// Add POV at index 0 if neither count nor quality tag
assert.strictEqual(
  PromptToolbar.setPovInPrompt("outdoors, night", true),
  "pov, outdoors, night"
);

// 5. Presets validation
const totalPoses = PromptToolbar.POSE_PRESETS.reduce((sum, g) => sum + g.items.length, 0);
assert.strictEqual(totalPoses >= 28, true, `Expected >= 28 poses, got ${totalPoses}`);
const totalAngles = PromptToolbar.ANGLE_PRESETS.reduce((sum, g) => sum + g.items.length, 0);
assert.strictEqual(totalAngles, 11, `Expected 11 angles, got ${totalAngles}`);

// 6. insertTagSetIntoPrompt with Smart Replacement & Cascade
// Case A: Angle shot cascade clears previous angle_shot and angle_focus
// If initial prompt has "from behind, lower body, ass focus"
// and we add "full body, wide shot, extreme perspective" (has angle_shot)
const initialWithAssFocus = "1girl, from behind, lower body, ass focus, bedroom";
const angleWidePreset = "full body, wide shot, fisheye, extreme perspective, foreshortening";
const replacedWithWide = PromptToolbar.insertTagSetIntoPrompt(
  initialWithAssFocus,
  angleWidePreset,
  "angle",
  true
);

// Check that ass focus and lower body were removed and full body/wide shot added
assert.strictEqual(replacedWithWide.includes("ass focus"), false, "ass focus should be cleared by angle_shot cascade");
assert.strictEqual(replacedWithWide.includes("lower body"), false, "lower body should be replaced by full body");
assert.strictEqual(replacedWithWide.includes("full body"), true, "full body should be inserted");
assert.strictEqual(replacedWithWide.includes("bedroom"), true, "bedroom should be preserved");

// Case B: Non angle_shot angle preset preserves existing angle_shot (e.g. full body)
const initialWithFullBody = "1girl, full body, looking at viewer, bedroom";
const angleDutch = "dutch angle, dynamic angle, foreshortening, perspective, depth of field";
const replacedWithDutch = PromptToolbar.insertTagSetIntoPrompt(
  initialWithFullBody,
  angleDutch,
  "angle",
  true
);
assert.strictEqual(replacedWithDutch.includes("full body"), true, "full body should be preserved when no angle_shot in new preset");
assert.strictEqual(replacedWithDutch.includes("dutch angle"), true, "dutch angle should be inserted");

// Case C: Pose replacement
// Initial has "sitting, on chair, knees apart"
// Add "lying, on back, spread legs, knees up"
const initialPose = "1girl, sitting, on chair, knees apart, bedroom";
const newPose = "lying, on back, spread legs, knees up";
const replacedPose = PromptToolbar.insertTagSetIntoPrompt(
  initialPose,
  newPose,
  "pose",
  true
);
assert.strictEqual(replacedPose.includes("sitting"), false, "old pose_base sitting should be replaced");
assert.strictEqual(replacedPose.includes("on chair"), false, "old pose_base on chair should be replaced");
assert.strictEqual(replacedPose.includes("lying"), true, "new pose lying should be inserted");
assert.strictEqual(replacedPose.includes("on back"), true, "new pose on back should be inserted");
assert.strictEqual(replacedPose.includes("bedroom"), true, "bedroom should remain at the end");

// Case D: Pose insertion before background
const promptNoPose = "1girl, indoors, bedroom";
const poseToAdd = "standing, looking back";
const resultWithPose = PromptToolbar.insertTagSetIntoPrompt(
  promptNoPose,
  poseToAdd,
  "pose",
  true
);
// "standing, looking back" should be inserted BEFORE "indoors, bedroom"
const standingIdx = resultWithPose.indexOf("standing");
const indoorsIdx = resultWithPose.indexOf("indoors");
assert.strictEqual(standingIdx < indoorsIdx, true, "pose should be inserted before tail background tags");

// Case E: smartReplace = false
const rawCombined = PromptToolbar.insertTagSetIntoPrompt(
  "1girl, sitting",
  "standing",
  "pose",
  false
);
assert.strictEqual(rawCombined.includes("sitting"), true);
assert.strictEqual(rawCombined.includes("standing"), true);

console.log("All PromptToolbar unit tests passed successfully!");
