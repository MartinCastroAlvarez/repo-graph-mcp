import { App } from "./app";
import { slugify } from "./util";

describe("App", () => {
  it("renders", () => {
    App();
  });
  it("slugifies", () => {
    expect(slugify("A B")).toBe("a-b");
  });
});
