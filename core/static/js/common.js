function validatePassword(str) {
  return /^(?=.*?[A-Z])(?=.*?[a-z])(?=.*?[0-9])(?=.*?[#?!@$%^&*-]).{8,}$/.test(str);
}

function validateEmail(str){
  return /(?:[a-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*|"(?:[\x01-\x08\x0b\x0c\x0e-\x1f\x21\x23-\x5b\x5d-\x7f]|\\[\x01-\x09\x0b\x0c\x0e-\x7f])*")@(?:(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]*[a-z0-9])?|\[(?:(?:(2(5[0-5]|[0-4][0-9])|1[0-9][0-9]|[1-9]?[0-9]))\.){3}(?:(2(5[0-5]|[0-4][0-9])|1[0-9][0-9]|[1-9]?[0-9])|[a-z0-9-]*[a-z0-9]:(?:[\x01-\x08\x0b\x0c\x0e-\x1f\x21-\x5a\x53-\x7f]|\\[\x01-\x09\x0b\x0c\x0e-\x7f])+)\])/.test(str);
}

function isShallowEq(obj1, obj2) {
  for (const [key, value] of Object.entries(obj1)) {
    if (obj2[key] !== value) return false;
  }
  for (const [key, value] of Object.entries(obj2)) {
    if (obj1[key] !== value) return false;
  }
  return true;
}

function buildSelectOptions(source, selectedId, exclude=[]) {
  const options = [];
  for (const [id, label] of Object.entries(source)) {
    if(exclude.indexOf(id)==-1){
      options.push({
        id: id,
        label: label,
        selected: id === selectedId,
      });
    }
  }
  return options;
}
