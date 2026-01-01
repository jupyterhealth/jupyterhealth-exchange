require("@testing-library/jest-dom");

// Mock global functions
global.renderStudies = jest.fn(async (params = {}) => {
  const response = await global.fetch("/api/v1/studies");
  const data = await response.json();

  const studiesList = document.getElementById("study");
  studiesList.innerHTML = "";

  if (data.results && Array.isArray(data.results)) {
    data.results.forEach((study) => {
      const studyElement = document.createElement("div");
      studyElement.className = "study-item";

      if (study.icon_url) {
        const icon = document.createElement("img");
        icon.src = study.icon_url;
        icon.alt = `${study.name} icon`;
        studyElement.appendChild(icon);
      }

      const name = document.createElement("span");
      name.textContent = study.name;
      studyElement.appendChild(name);

      studiesList.appendChild(studyElement);
    });
  }
});

// Mock fetch
global.fetch = jest.fn((url, options) => {
  if (url.includes("/studies")) {
    const study = {
      name: "Test Study",
      icon_url: "https://example.com/study-icon.png",
    };
    document.getElementById("study").innerHTML = `
      <div class="study-item">
        <img src="${study.icon_url}" icon">
        <span>${study.name}</span>
      </div>
    `;
    return Promise.resolve({
      json: () =>
        Promise.resolve({
          results: [study],
        }),
      ok: true,
    });
  }
  return Promise.resolve({
    json: () => Promise.resolve({ results: [] }),
    ok: true,
  });
});
