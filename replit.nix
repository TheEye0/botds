{ pkgs }: {
  deps = [
    pkgs.python311Full
    pkgs.python311Packages.pip
    pkgs.python311Packages.setuptools
    pkgs.python311Packages.wheel
  ];

  postInstall = ''
    pip install discord.py python-dotenv groq google-search-results
  '';
}

