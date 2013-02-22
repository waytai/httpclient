from setuptools import setup, find_packages

version = '0.0'


setup(name='httpclient',
      version=version,
      author='Nikolay Kim',
      author_email='fafhrd91@gmail.com',
      url='https://github.com/fafhrd91/httpclient/',
      packages=find_packages(),
      include_package_data=True,
      zip_safe=False,
      )
