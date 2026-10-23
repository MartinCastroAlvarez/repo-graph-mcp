import { slugify, Cache } from "./util";
import * as React from "react";

function Title(props: { text: string }) {
  return <h1>{props.text}</h1>;
}

export function App() {
  const cache = new Cache();
  cache.put("k", "v");
  return <Title text={slugify("Hello World")} />;
}

export default App;
