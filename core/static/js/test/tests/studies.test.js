import { jest, describe, test, expect, beforeEach } from "@jest/globals";

describe("Studies", () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <div id="study"></div>
    `;
  });

  test("should render study page with correct data", async () => {
    await renderStudies();
    const studiesList = document.getElementById("study");
    expect(studiesList).toHaveTextContent("Test Study");
    expect(studiesList.querySelector("img")).toHaveAttribute(
      "src",
      "https://example.com/study-icon.png"
    );
  });
});
