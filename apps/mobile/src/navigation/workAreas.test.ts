import { WORK_AREAS } from "@/src/navigation/workAreas";

describe("primary work areas", () => {
  it("exposes exactly the two participant-approved peer areas", () => {
    expect(WORK_AREAS.map((area) => area.label)).toEqual([
      "To review",
      "In progress",
    ]);
  });
});
