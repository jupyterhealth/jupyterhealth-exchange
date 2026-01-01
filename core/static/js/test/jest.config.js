module.exports = {
  testEnvironment: "jsdom",
  transform: {
    "^.+\\.js$": ["babel-jest", { configFile: "./babel.config.js" }],
  },
  moduleNameMapper: {
    "\\.(css|less|scss|sass)$": "identity-obj-proxy",
  },
  setupFilesAfterEnv: ["./jest.setup.js"],
  transformIgnorePatterns: ["/node_modules/(?!(@testing-library)/)"],
  testMatch: ["**/tests/**/*.js"],
  moduleFileExtensions: ["js", "json", "jsx"],
  testEnvironmentOptions: {
    customExportConditions: ["node", "node-addons"],
  },
};
