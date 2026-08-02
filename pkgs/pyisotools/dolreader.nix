# dolreader — reads and writes Nintendo's DOL executable format.
#
# A dependency of pyisotools and not in nixpkgs. Trivial pure-Python package;
# packaged here only because the thing that needs it is.
{
  lib,
  buildPythonPackage,
  fetchPypi,
  setuptools,
}:

buildPythonPackage rec {
  pname = "dolreader";
  version = "1.1.1";
  pyproject = true;

  src = fetchPypi {
    inherit pname version;
    hash = "sha256-mSb2gvMXmYlAJlHOgzjlynCzbYizTbT6a2m8twGJZvs=";
  };

  build-system = [ setuptools ];

  # No test suite ships with the sdist.
  doCheck = false;
  pythonImportsCheck = [ "dolreader" ];

  meta = {
    description = "Library for working with Nintendo's DOL format";
    homepage = "https://pypi.org/project/dolreader/";
    license = lib.licenses.mit;
  };
}
