import time

import numpy
import theano
from theano import tensor as T
from theano import sparse as S
from theano.tensor.shared_randomstreams import RandomStreams
import scipy.sparse


def symbolic(inputs):
    """
    Wrap a symbolic method so that it can also accept concrete arguments.
    The method will be compiled with the provided inputs and stored under
    the name of the method prefixed with '__'.
        
    Parameters
    ----------
    inputs: list
        inputs to use to compile the method (i.e. theano.tensor.matrix()).

    Returns
    -------
    wrapped_method: fn
    """
    def decorator(method):
        name = "__" + method.__name__
        
        def wrapper(self, *args):
            if isinstance(args[0], T.Variable):
                return method(self, *args)
            elif hasattr(self, name):
                return getattr(self, name)(*args)
            else:
                res = method(self, *inputs)
                
                if type(res) is tuple:
                    output, updates = res
                else:
                    output, updates = res, None
                
                setattr(self, name, theano.function(inputs, output,
                    updates=updates))
                
                return getattr(self, name)(*args)
        
        return wrapper
    
    return decorator


class BinaryRBM(object):
    """
    Restricted Boltzmann Machine (RBM)
    
    A Restricted Boltzmann Machine with binary visible units and
    binary hiddens. Parameters are estimated using Stochastic Maximum
    Likelihood (SML).
    
    Examples
    ========
    
    >>> import numpy, rbm
    >>> X = numpy.array([[0, 0, 0], [0, 1, 1], [1, 0, 1], [1, 1, 1]])
    >>> model = rbm.RBM(n_hiddens=2)
    >>> model.fit(X)
    """
    def __init__(self, n_hiddens=1024,
                       W=None,
                       c=None,
                       b=None,
                       K=1,
                       epsilon=0.1,
                       n_samples=10,
                       epochs=20):
        """
        Initialize an RBM.
        
        Parameters
        ----------
        n_hiddens : int, optional
            Number of binary hidden units
        W : array-like, shape (n_visibles, n_hiddens), optional
            Weight matrix, where n_visibles in the number of visible
            units and n_hiddens is the number of hidden units.
        c : array-like, shape (n_hiddens,), optional
            Biases of the hidden units
        b : array-like, shape (n_visibles,), optional
            Biases of the visible units
        K : int, optional
            Number of MCMC steps to perform on the negative chain
            after each gradient step.
        epsilon : float, optional
            Learning rate to use during learning
        n_samples : int, optional
            Number of fantasy particles to use during learning
        epochs : int, optional
            Number of epochs to perform during learning
        """
        self.n_hiddens = n_hiddens
        self._W = theano.shared(numpy.array([[]], dtype=theano.config.floatX)
            if W == None else W)
        self._c = theano.shared(numpy.array([], dtype=theano.config.floatX)
            if c == None else c)
        self._b = theano.shared(numpy.array([], dtype=theano.config.floatX)
            if b == None else b)
        self.K = K
        self.epsilon = epsilon
        self.n_samples = n_samples
        self.epochs = epochs
        self.h_samples = theano.shared(numpy.array([[]],
            dtype=theano.config.floatX))
        self.rng = RandomStreams(numpy.random.randint(2**30))
    
    @property
    def W(self):
        return self._W.get_value()
    
    @W.setter
    def W(self, val):
        self._W.set_value(val)
    
    @property
    def b(self):
        return self._b.get_value()
    
    @b.setter
    def b(self, val):
        self._b.set_value(val)
    
    @property
    def c(self):
        return self._c.get_value()
    
    @c.setter
    def c(self, val):
        self._c.set_value(val)
    
    @symbolic([T.matrix('v')])
    def mean_h(self, v):
        """
        Computes the probabilities P({\bf h}_j=1|{\bf v}).
        
        Parameters
        ----------
        v: array-like, shape (n_samples, n_visibles)

        Returns
        -------
        h: array-like, shape (n_samples, n_hiddens)
        """
        return T.nnet.sigmoid(T.dot(v, self._W) + self._c)
    
    @symbolic([T.matrix('v')])
    def sample_h(self, v):
        """
        Sample from the distribution P({\bf h}|{\bf v}).
        
        Parameters
        ----------
        v: array-like, shape (n_samples, n_visibles)
        
        Returns
        -------
        h: array-like, shape (n_samples, n_hiddens)
        """
        return self.rng.binomial(n=1, p=self.mean_h(v),
            dtype=theano.config.floatX)
    
    @symbolic([T.matrix('h')])
    def mean_v(self, h):
        """
        Computes the probabilities P({\bf v}_i=1|{\bf h}).
        
        Parameters
        ----------
        h: array-like, shape (n_samples, n_hiddens)
        
        Returns
        -------
        v: array-like, shape (n_samples, n_visibles)
        """
        return T.nnet.sigmoid(T.dot(h, self._W.T) + self._b)
    
    @symbolic([T.matrix('h')])
    def sample_v(self, h):
        """
        Sample from the distribution P({\bf v}|{\bf h}).
        
        Parameters
        ----------
        h: array-like, shape (n_samples, n_hiddens)
        
        Returns
        -------
        v: array-like, shape (n_samples, n_visibles)
        """
        return self.rng.binomial(n=1, p=self.mean_v(h),
            dtype=theano.config.floatX)
    
    @symbolic([T.matrix('v')])
    def free_energy(self, v):
        """
        Computes the free energy
        \mathcal{F}({\bf v}) = - \log \sum_{\bf h} e^{-E({\bf v},{\bf h})}.
        
        Parameters
        ----------
        v: array-like, shape (n_samples, n_visibles)
        
        Returns
        -------
        free_energy: array-like, shape (n_samples,)
        """
        return - T.dot(v, self._b) \
            - T.log(1. + T.exp(T.dot(v, self._W) + self._c)).sum(1)
    
    @symbolic([T.matrix('v')])
    def gibbs(self, v):
        """
        Perform one Gibbs MCMC sampling step.
        
        Parameters
        ----------
        v: array-like, shape (n_samples, n_visibles)
        
        Returns
        -------
        v_new: array-like, shape (n_samples, n_visibles)
        """
        h_ = self.sample_h(v)
        v_ = self.sample_v(h_)
        
        return v_
    
    @symbolic([T.matrix('v_pos')])
    def _fit(self, v_pos):
        """
        Adjust the parameters to maximize the likelihood of {\bf v}
        using Stochastic Maximum Likelihood (SML).
        
        Parameters
        ----------
        v_pos: array-like, shape (n_samples, n_visibles)
        """
        h_neg = self.h_samples
        for _ in range(self.K):
            v_neg = self.sample_v(h_neg)
            h_neg = self.sample_h(v_neg)
        
        cost = T.mean(self.free_energy(v_pos)) - T.mean(self.free_energy(v_neg))
        
        params = [self._W, self._b, self._c]
        gparams = T.grad(cost, params, consider_constant=[v_neg])
        
        updates = {}
        for p, gp in zip(params, gparams):
            updates[p] = p - self.epsilon * gp
        
        updates[self.h_samples] = h_neg
        
        loss = self._pseudo_likelihood(v_pos, updates)
        
        return loss, updates
    
    def _pseudo_likelihood(self, v_pos, updates):
        """
        Theano graph for the calculation of the pseudo-likelihood.
        
        Parameters
        ----------
        v_pos: array-like, shape (n_samples, n_visibles)
        updates: dict
            An index shared variable must be added to the updates.
        
        Returns
        -------
        pl: float
        """
        bit_i = theano.shared(value=0, name='bit_i')

        fe_xi = self.free_energy(v_pos)

        fe_xi_ = self.free_energy(T.set_subtensor(v_pos[:, bit_i],
            1 - v_pos[:, bit_i]))

        updates[bit_i] = (bit_i + 1) % v_pos.shape[1]
        
        return T.mean(v_pos.shape[1] * T.log(T.nnet.sigmoid(fe_xi_ - fe_xi)))
    
    def fit(self, X, verbose=False, callback=None, project=lambda x: x):
        """
        Fit the model to the data X.

        Parameters
        ----------
        X: array-like, shape (n_samples, n_features)
            Training data, where n_samples in the number of samples
            and n_features is the number of features.
        """
        n_in = project(X[[0]]).shape[1]
        if self.W.shape[1] == 0:
            self.W = numpy.asarray(numpy.random.normal(0, 0.01,
                (n_in, self.n_hiddens)), dtype=theano.config.floatX)
            self.c = numpy.zeros(self.n_hiddens, dtype=theano.config.floatX)
            self.b = numpy.zeros(n_in, dtype=theano.config.floatX)
            self.h_samples.set_value(numpy.zeros(
                (self.n_samples, self.n_hiddens), dtype=theano.config.floatX))

        inds = range(X.shape[0])

        numpy.random.shuffle(inds)

        n_batches = int(numpy.ceil(len(inds) / float(self.n_samples)))

        for epoch in range(self.epochs):
            loss = []
            begin = time.time()
            for minibatch in range(n_batches):
                loss.append(self._fit(project(X[inds[minibatch::n_batches]])))
            end = time.time()

            if verbose:
                er = numpy.mean(loss)

                print "Epoch %d, Reconstruction Error = %.2f, time = %.2f" \
                    % (epoch, er, end-begin)

                if callback != None:
                    callback(self, epoch)


class GaussianRBM(object):
    """
    Restricted Boltzmann Machine (RBM)
    
    A Restricted Boltzmann Machine with binary visible units and
    binary hiddens. Parameters are estimated using Stochastic Maximum
    Likelihood (SML).
    
    Examples
    ========
    
    >>> import numpy, rbm
    >>> X = numpy.array([[0, 0, 0], [0, 1, 1], [1, 0, 1], [1, 1, 1]])
    >>> model = rbm.RBM(n_hiddens=2)
    >>> model.fit(X)
    """
    def __init__(self, n_hiddens=1024,
                       W=None,
                       c=None,
                       b=None,
                       K=1,
                       epsilon=0.1,
                       n_samples=10,
                       epochs=20):
        """
        Initialize an RBM.
        
        Parameters
        ----------
        n_hiddens : int, optional
            Number of binary hidden units
        W : array-like, shape (n_visibles, n_hiddens), optional
            Weight matrix, where n_visibles in the number of visible
            units and n_hiddens is the number of hidden units.
        c : array-like, shape (n_hiddens,), optional
            Biases of the hidden units
        b : array-like, shape (n_visibles,), optional
            Biases of the visible units
        K : int, optional
            Number of MCMC steps to perform on the negative chain
            after each gradient step.
        epsilon : float, optional
            Learning rate to use during learning
        n_samples : int, optional
            Number of fantasy particles to use during learning
        epochs : int, optional
            Number of epochs to perform during learning
        """
        self.n_hiddens = n_hiddens
        self._W = theano.shared(numpy.array([[]], dtype=theano.config.floatX)
            if W == None else W)
        self._c = theano.shared(numpy.array([], dtype=theano.config.floatX)
            if c == None else c)
        self._b = theano.shared(numpy.array([], dtype=theano.config.floatX)
            if b == None else b)
        self.K = K
        self.epsilon = epsilon
        self.n_samples = n_samples
        self.epochs = epochs
        self.h_samples = theano.shared(numpy.array([[]],
            dtype=theano.config.floatX))
        self.rng = RandomStreams(numpy.random.randint(2**30))
    
    @property
    def W(self):
        return self._W.get_value()
    
    @W.setter
    def W(self, val):
        self._W.set_value(val)
    
    @property
    def b(self):
        return self._b.get_value()
    
    @b.setter
    def b(self, val):
        self._b.set_value(val)
    
    @property
    def c(self):
        return self._c.get_value()
    
    @c.setter
    def c(self, val):
        self._c.set_value(val)
    
    @symbolic([T.matrix('v')])
    def mean_h(self, v):
        """
        Computes the probabilities P({\bf h}_j=1|{\bf v}).
        
        Parameters
        ----------
        v: array-like, shape (n_samples, n_visibles)

        Returns
        -------
        h: array-like, shape (n_samples, n_hiddens)
        """
        return T.nnet.sigmoid(T.dot(v, self._W) + self._c)
    
    @symbolic([T.matrix('v')])
    def sample_h(self, v):
        """
        Sample from the distribution P({\bf h}|{\bf v}).
        
        Parameters
        ----------
        v: array-like, shape (n_samples, n_visibles)
        
        Returns
        -------
        h: array-like, shape (n_samples, n_hiddens)
        """
        return self.rng.binomial(n=1, p=self.mean_h(v),
            dtype=theano.config.floatX)
    
    @symbolic([T.matrix('h')])
    def mean_v(self, h):
        """
        Computes the probabilities P({\bf v}_i=1|{\bf h}).
        
        Parameters
        ----------
        h: array-like, shape (n_samples, n_hiddens)
        
        Returns
        -------
        v: array-like, shape (n_samples, n_visibles)
        """
        return T.dot(h, self._W.T) + self._b
    
    @symbolic([T.matrix('h')])
    def sample_v(self, h):
        """
        Sample from the distribution P({\bf v}|{\bf h}).
        
        Parameters
        ----------
        h: array-like, shape (n_samples, n_hiddens)
        
        Returns
        -------
        v: array-like, shape (n_samples, n_visibles)
        """
        return self.rng.normal(std=1, avg=self.mean_v(h),
            dtype=theano.config.floatX)
    
    @symbolic([T.matrix('v')])
    def free_energy(self, v):
        """
        Computes the free energy
        \mathcal{F}({\bf v}) = - \log \sum_{\bf h} e^{-E({\bf v},{\bf h})}.
        
        Parameters
        ----------
        v: array-like, shape (n_samples, n_visibles)
        
        Returns
        -------
        free_energy: array-like, shape (n_samples,)
        """
        return - ((v - self._b)**2).sum(1) \
            - T.log(1. + T.exp(T.dot(v, self._W) + self._c)).sum(1)
    
    @symbolic([T.matrix('v')])
    def gibbs(self, v):
        """
        Perform one Gibbs MCMC sampling step.
        
        Parameters
        ----------
        v: array-like, shape (n_samples, n_visibles)
        
        Returns
        -------
        v_new: array-like, shape (n_samples, n_visibles)
        """
        h_ = self.sample_h(v)
        v_ = self.sample_v(h_)
        
        return v_
    
    @symbolic([T.matrix('v_pos')])
    def _fit(self, v_pos):
        """
        Adjust the parameters to maximize the likelihood of {\bf v}
        using Stochastic Maximum Likelihood (SML).
        
        Parameters
        ----------
        v_pos: array-like, shape (n_samples, n_visibles)
        """
        h_neg = self.h_samples
        for _ in range(self.K):
            v_neg = self.mean_v(h_neg) # Use mean to reduce instability
            h_neg = self.sample_h(v_neg)
        
        cost = T.mean(self.free_energy(v_pos)) - T.mean(self.free_energy(v_neg))
        
        params = [self._W, self._b, self._c]
        gparams = T.grad(cost, params, consider_constant=[v_neg])
        
        updates = {}
        for p, gp in zip(params, gparams):
            updates[p] = p - self.epsilon * gp
        
        updates[self.h_samples] = h_neg
        
        loss = self._reconstruction_error(v_pos)
        
        return loss, updates
    
    def _reconstruction_error(self, v_pos):
        """
        Theano graph for the calculation of the reconstruction error.
        
        Parameters
        ----------
        v_pos: array-like, shape (n_samples, n_visibles)
        
        Returns
        -------
        error: float
        """
        h = self.mean_h(v_pos)
        z = self.mean_v(h)
        
        return ((v_pos - z)**2).sum(1).mean()
    
    def fit(self, X, verbose=False, callback=None, project=lambda x: x):
        """
        Fit the model to the data X.
        
        Parameters
        ----------
        X: array-like, shape (n_samples, n_features)
            Training data, where n_samples in the number of samples
            and n_features is the number of features.
        """
        n_in = project(X[[0]]).shape[1]
        if self.W.shape[1] == 0:
            self.W = numpy.asarray(numpy.random.normal(0, 0.01,
                (n_in, self.n_hiddens)), dtype=theano.config.floatX)
            self.c = numpy.zeros(self.n_hiddens, dtype=theano.config.floatX)
            self.b = numpy.zeros(n_in, dtype=theano.config.floatX)
            self.h_samples.set_value(numpy.zeros(
                (self.n_samples, self.n_hiddens), dtype=theano.config.floatX))
        
        inds = range(X.shape[0])
        
        numpy.random.shuffle(inds)
        
        n_batches = int(numpy.ceil(len(inds) / float(self.n_samples)))
        
        for epoch in range(self.epochs):
            loss = []
            begin = time.time()
            for minibatch in range(n_batches):
                loss.append(self._fit(project(X[inds[minibatch::n_batches]])))
            end = time.time()
            
            if verbose:
                er = numpy.mean(loss)
                
                print "Epoch %d, Reconstruction Error = %.2f, time = %.2f" \
                    % (epoch, er, end-begin)
                
                if callback != None:
                    callback(self, epoch)

class SetRBM(object):
    """
    The Restricted Boltzmann Machine learning algorithm.
    """
    def __init__(self, n_visibles, n_hiddens, n_classes,
            W=None, U=None, b=None, c=None, d=None, learning_rate=0.1, K=1):
        self.n_visibles = n_visibles
        self.n_hiddens = n_hiddens
        self.n_classes = n_classes
        
        self.x = T.matrix('x')
        self.y = T.vector('y')
        
        if W is None:
            W_value = numpy.asarray( numpy.random.normal(
                      loc=0,
                      scale=0.01,
                      size = (n_visibles, n_hiddens)),
                      dtype = theano.config.floatX)
            W = theano.shared(value=W_value, name='W')

        if U is None:
            U_value = numpy.asarray( numpy.random.normal(
                      loc=0,
                      scale=0.01,
                      size = (n_classes, n_hiddens)),
                      dtype = theano.config.floatX)
            U = theano.shared(value=U_value, name='W')

        if b is None :
            b = theano.shared(value=numpy.zeros(n_hiddens,
                                dtype=theano.config.floatX), name='b')

        if c is None :
            c = theano.shared(value=numpy.zeros(n_visibles,
                                dtype=theano.config.floatX),name='c')
        
        if d is None :
            d = theano.shared(value=numpy.zeros(n_classes,
                                dtype=theano.config.floatX),name='d')
        
        self.W = W
        self.U = U
        self.b = b
        self.c = c
        self.d = d
        self.params = [self.W, self.U, self.b, self.c, self.d]
        self.theano_rng = RandomStreams(numpy.random.randint(2**30))
        
        self.learning_rate = theano.shared(numpy.asarray(learning_rate,
            dtype=theano.config.floatX))
        self.K = K
        
        cost, updates = self.__train()
        self.train = theano.function([self.x, self.y], cost,
            updates=updates)
        self.trainables = map(lambda x: x, updates)
        
        # TODO need way to compute to marginalize g from y
        #self.transform = theano.function([self.x], self._mean_g(self.x).sum(0))
        self.output = theano.function([self.x], self._output(self.x))
    
    def _free_energy(self, x, y):
        bias_term = T.dot(y, self.d) + T.dot(x, self.c)
        softmax_x = T.log(T.exp(self._softminus(
            T.dot(x, self.W) + self.b)).sum(0))
        hidden_term = T.nnet.softplus(T.dot(y, self.U) + softmax_x).sum()
        
        return -bias_term - hidden_term
    
    def _output(self, x):
        softmax_x = T.log(T.exp(self._softminus(
            T.dot(x, self.W) + self.b)).sum(0))
        output = -T.nnet.softplus(self.U + softmax_x).sum(1)
        
        return T.argmax(T.nnet.softmax(output))
    
    def _softminus(self, x):
        return x - T.nnet.softplus(x)
    
    def _act(self, x, y):
        return self._softminus(self.b + T.dot(x, self.W)) + T.dot(y, self.U)
    
    def _mean_g(self, x, y):
        act = self._act(x, y)
        
        return T.exp(act) / (1. + T.exp(act).sum(0)), 1. / (1. + T.exp(act).sum(0))
    
    def _mean_h(self, g, x):
        return T.maximum(g, T.nnet.sigmoid(T.dot(x, self.W) + self.b))
    
    def _mean_x(self, h):
        return T.dot(h, self.W.T) + self.c
    
    def _mean_y(self, g):
        return T.nnet.softmax(T.dot(g, self.U.T).sum(0) + self.d)
    
    def _sample_g(self, x, y):
        g_mean, g_zeros = self._mean_g(x, y)
        
        g_mean = T.concatenate((g_zeros.dimshuffle('x', 0), g_mean))
        
        g_sample = self.theano_rng.multinomial(n=1, pvals=g_mean.T,
                dtype=theano.config.floatX).T[1:]
        
        return g_sample
    
    def _sample_h(self, g, x):
        h_mean = self._mean_h(g, x)
        
        h_sample = self.theano_rng.binomial(size=h_mean.shape, n=1, p=h_mean,
                dtype=theano.config.floatX)
        
        return h_sample
    
    def _sample_x(self, h):
        x_mean = self._mean_x(h)
        
        x_sample = self.theano_rng.binomial(size=x_mean.shape, n=1, p=x_mean,
                dtype = theano.config.floatX)
        
        return x_sample
    
    def _sample_y(self, g):
        y_mean = self._mean_y(g)
        
        y_sample = self.theano_rng.multinomial(n=1, pvals=y_mean,
                dtype = theano.config.floatX)
        
        return y_sample
    
    def __train(self):
        nx_samples = self.x
        ng_samples = self._sample_g(self.x, self.y)
        for _ in range(self.K):
            nh_samples = self._sample_h(ng_samples, nx_samples)
        
            nx_samples = self._mean_x(nh_samples)
            
            ny_samples = self._sample_y(ng_samples)
            
            ng_samples = self._sample_g(nx_samples, ny_samples)
        
        cost = T.mean(self._free_energy(self.x, self.y)) \
            - T.mean(self._free_energy(nx_samples, ny_samples))
        
        gparams = T.grad(cost, self.params,
            consider_constant=[nx_samples, ny_samples])
        
        updates = {}
        for gparam, param in zip(gparams, self.params):
            updates[param] = param - gparam * T.cast(self.learning_rate,
                dtype=theano.config.floatX)
        
        monitoring_cost = T.nnet.binary_crossentropy(self.y, ny_samples).mean()

        return monitoring_cost, updates
    
    def save(self, tag=None):
        if tag == None:
            tag = ""
        else:
            tag = "_%s" % tag
        
        numpy.save("rbm_W%s.npy" % tag, self.W.get_value(borrow=True))
        numpy.save("rbm_U%s.npy" % tag, self.U.get_value(borrow=True))
        numpy.save("rbm_b%s.npy" % tag, self.b.get_value(borrow=True))
        numpy.save("rbm_c%s.npy" % tag, self.c.get_value(borrow=True))
        numpy.save("rbm_d%s.npy" % tag, self.d.get_value(borrow=True))


class NeuralNetworkLayer(object):
    """
    A Neural Network layer with that takes the activation function as parameter.
    """
    def __init__(self, x, n_in, n_out, activation, W=None, b=None):
        self.n_in = n_in
        self.n_out = n_out
        self.input = x

        W_values = numpy.asarray( numpy.random.uniform(
            low  = - numpy.sqrt(6./(n_in+n_out)),
            high =   numpy.sqrt(6./(n_in+n_out)),
            size = (n_in, n_out)), dtype = theano.config.floatX)
        
        if W == None:
            self.W = theano.shared(W_values, name = 'W%dx%d' % (n_in, n_out))
        else:
            self.W = W

        if b == None:
            self.b = theano.shared(numpy.zeros((n_out,), dtype=theano.config.floatX), name = 'b')
        else:
            self.b = b
        
        self.linear_output = T.dot(x, self.W)
        
        self.output = activation(self.linear_output + self.b)

    def reset(self):
        """
        Reset the weights to suitable random values.
        """
        W_values = numpy.asarray( numpy.random.uniform(
            low  = - numpy.sqrt(6./(self.n_in+self.n_out)),
            high =   numpy.sqrt(6./(self.n_in+self.n_out)),
            size = (self.n_in, self.n_out)), dtype = theano.config.floatX)

        self.b.value = numpy.zeros((self.n_out,), dtype=theano.config.floatX)
        self.W.value = W_values


class LogisticLayer(NeuralNetworkLayer):
    def __init__(self, x, n_in, n_out, **kwargs):
        super(LogisticLayer, self).__init__(x, n_in, n_out, T.nnet.sigmoid, **kwargs)


class RectifierLayer(NeuralNetworkLayer):
    def __init__(self, x, n_in, n_out, mask=True, **kwargs):
        if mask:
          activation = lambda x: numpy.asarray(numpy.sign(numpy.random.uniform(low=-1, high=1, size=(n_out,))), dtype=theano.config.floatX) * T.maximum(x, 0)
        else:
          activation = lambda x: T.maximum(x, 0)
        super(RectifierLayer, self).__init__(x, n_in, n_out, activation, **kwargs)


class TanhLayer(NeuralNetworkLayer):
    def __init__(self, x, n_in, n_out, **kwargs):
        super(TanhLayer, self).__init__(x, n_in, n_out, T.tanh, **kwargs)


class SoftmaxLayer(NeuralNetworkLayer):
    def __init__(self, x, n_in, n_out, **kwargs):
        super(SoftmaxLayer, self).__init__(x, n_in, n_out, T.nnet.softmax, **kwargs)


class SoftplusLayer(NeuralNetworkLayer):
    def __init__(self, x, n_in, n_out, **kwargs):
        super(SoftplusLayer, self).__init__(x, n_in, n_out, T.nnet.softplus, **kwargs)


class LinearLayer(NeuralNetworkLayer):
    def __init__(self, x, n_in, n_out, **kwargs):
        super(LinearLayer, self).__init__(x, n_in, n_out, lambda x: x, **kwargs)


class SquaredLayer(NeuralNetworkLayer):
    def __init__(self, x, n_in, n_out, **kwargs):
        super(SquaredLayer, self).__init__(x, n_in, n_out, lambda x: x**2, **kwargs)


class NeuralNetworkTrainer(object):
    """
    Train the given layers of a neural network with the given loss function.
    """
    def __init__(self, inputs, cost, layers, learning_rate=0.01, momentum=None, **kw):
        params = [layer.W for layer in layers] + [layer.b for layer in layers]
        
        self.learning_rate = theano.shared(numpy.asarray(learning_rate, dtype=theano.config.floatX))
        
        gparams = []
        for param in params:
            gparam  = T.grad(cost, param)
            gparams.append(gparam)

        updates = []
        for param, gparam in zip(params, gparams):
            if momentum:
                memory = theano.shared(param.get_value() * 0.)
                updates.append((param, param - memory))
                updates.append((memory, momentum * memory + learning_rate * gparam))
            else:
                updates.append((param, param - learning_rate * gparam))

        self._train = theano.function(inputs, outputs=cost, updates=updates)


    def train(self, *args):
        return self._train(*args)


class MLP(object):
    def __init__(self, n_in, layers, hidden_dropout=0.5, l2=None, **kwargs):
        x = T.fmatrix('x')
        y = T.lvector('y')

        type_map = {
            'L' : LogisticLayer,
            'R' : RectifierLayer,
            'S' : SoftmaxLayer,
            'Sp' : SoftplusLayer,
            'T' : TanhLayer,
            'Li' : LinearLayer,
            'Sq' : SquaredLayer,
        }
        
        self.rng = RandomStreams(numpy.random.randint(2**30))
        
        self.layers = []
        # Create hidden layers
        for i, layer in enumerate(layers):
            layer_type = layer[0]
            layer_size = layer[1]

            if i == 0:
                layer_input = x
                layer_n_in = n_in
            else:
                layer_input = self.layers[-1].output
                layer_n_in = self.layers[-1].n_out

            if i == len(layers) - 1:
                layer_input = T.max(layer_input, axis=0)
                layer_input = layer_input * self.rng.binomial(n=1, p=hidden_dropout,
                    dtype=theano.config.floatX) / hidden_dropout
            
            xargs = {}

            if layer_type == 'R' and layer == layers[-1]:
                xargs['mask'] = False

            layer = type_map[layer_type](layer_input,
                                         layer_n_in,
                                         layer_size,
                                         **xargs)

            self.layers.append(layer)
        
        self.clean_layers = []
        # Create hidden layers
        for i, layer in enumerate(layers):
            layer_type = layer[0]
            layer_size = layer[1]

            if i == 0:
                layer_input = x
                layer_n_in = n_in
            else:
                layer_input = self.clean_layers[-1].output
                layer_n_in = self.clean_layers[-1].n_out

            if i == len(layers) - 1:
                layer_input = T.max(layer_input, axis=0)

            xargs = {}

            if layer_type == 'R' and layer == layers[-1]:
                xargs['mask'] = False

            layer = type_map[layer_type](layer_input,
                                         layer_n_in,
                                         layer_size,
                                         W=self.layers[i].W,
                                         b=self.layers[i].b,
                                         **xargs)

            self.clean_layers.append(layer)
        
        self._output = theano.function([x], T.argmax(self.clean_layers[-1].output, axis=1))
        
        self.transform = theano.function([x], T.max(self.clean_layers[-2].output, axis=0))
        
        loss = -T.mean(T.log(self.layers[-1].output)[T.arange(y.shape[0]), y])
        
        if l2 != None:
            loss += l2 * sum([(l.W**2).sum() for l in self.layers])
        
        self.trainer = NeuralNetworkTrainer([x, y], loss, self.layers, **kwargs)

        
    def train(self, *args):
        return self.trainer.train(*args)


    def output(self, x, batch_size=None):
        if batch_size:
            out = []
            n_batches = int(numpy.ceil(x.shape[0] / float(batch_size)))
            for n in range(n_batches):
                out.append(self._output(x[n * batch_size : (n+1) * batch_size ]))
            return numpy.concatenate(out)
        else:
            return self._output(x)

    def save(self):
        for i, layer in enumerate(self.layers):
            numpy.save("W_%d.npy" % i, layer.W.get_value())
            numpy.save("b_%d.npy" % i, layer.b.get_value())


    def load(self):
        for i, layer in enumerate(self.layers):
            layer.W.set_value(numpy.load("W_%d.npy" % i))
            layer.b.set_value(numpy.load("b_%d.npy" % i))


    def reset(self):
        for layer in self.layers:
            layer.reset()


class RNN(object):
    def __init__(self, n_in, n_hiddens, n_out, learning_rate):
        self.x = T.matrix()
        self.y = T.matrix()
        self.h0 = theano.shared(numpy.zeros(n_hiddens,
            dtype=theano.config.floatX), name='h0')
        self.W = theano.shared(numpy.asarray(numpy.random.uniform(
            low  = - numpy.sqrt(6./(n_in+n_hiddens)),
            high =   numpy.sqrt(6./(n_in+n_hiddens)),
            size = (n_in, n_hiddens)), dtype = theano.config.floatX), name='W')
        self.U = theano.shared(numpy.asarray(numpy.random.uniform(
            low  = - numpy.sqrt(6./(n_hiddens+n_hiddens)),
            high =   numpy.sqrt(6./(n_hiddens+n_hiddens)),
            size = (n_hiddens, n_hiddens)), dtype = theano.config.floatX), name='U')
        self.V = theano.shared(numpy.asarray(numpy.random.uniform(
            low  = - numpy.sqrt(6./(n_hiddens+n_out)),
            high =   numpy.sqrt(6./(n_hiddens+n_out)),
            size = (n_hiddens, n_out)), dtype = theano.config.floatX), name='V')
        self.b = theano.shared(numpy.zeros((n_hiddens,),
            dtype=theano.config.floatX), name = 'b')
        self.c = theano.shared(numpy.zeros((n_out,),
            dtype=theano.config.floatX), name = 'c')
        self.params = [self.W, self.U, self.V, self.b, self.c]
        
        def step(x_t, h_tm1, W, U, V, b, c):
            h_t = T.tanh(T.dot(x_t, W) + T.dot(h_tm1, U) + b)
            y_t = T.nnet.sigmoid(T.dot(h_t, V) + c)
            return h_t, y_t

        [h, output], _ = theano.scan(step,
                                sequences=self.x,
                                outputs_info=[self.h0, None],
                                non_sequences=[self.W, self.U, self.V, self.b,
                                    self.c])
        cost = -(self.y*T.log(output) + (1.-self.y)*T.log(1.-output)).sum(1).mean()
        
        gparams = T.grad(cost, self.params)
        self.learning_rate = theano.shared(numpy.asarray(learning_rate,
            dtype=theano.config.floatX))
        
        updates = []
        for param, gparam in zip(self.params, gparams):
            updates.append((param, param
                - self.learning_rate * T.maximum(-15, T.minimum(gparam, 15.))))
        self.train = theano.function([self.x, self.y], outputs=cost,
            updates=updates)
        self.output = theano.function([self.x], outputs=T.argmax(output.mean(0)))    
        self.transform = theano.function([self.x], outputs=h.mean(0))
    
    def save(self):
        numpy.save("W.npy", self.W.get_value())
        numpy.save("U.npy", self.U.get_value())
        numpy.save("V.npy", self.V.get_value())
        numpy.save("b.npy", self.b.get_value())
        numpy.save("c.npy", self.c.get_value())


class AttrMLP(object):
    def __init__(self, n_in, layers, hidden_dropout=0.5, l2=None, **kwargs):
        x = T.fmatrix('x')
        y = T.lvector('y')

        type_map = {
            'L' : LogisticLayer,
            'R' : RectifierLayer,
            'S' : SoftmaxLayer,
            'Sp' : SoftplusLayer,
            'T' : TanhLayer,
            'Li' : LinearLayer,
            'Sq' : SquaredLayer,
        }
        
        self.rng = RandomStreams(numpy.random.randint(2**30))
        
        self.layers = []
        # Create hidden layers
        for i, layer in enumerate(layers):
            layer_type = layer[0]
            layer_size = layer[1]

            if i == 0:
                layer_input = x
                layer_n_in = n_in
            else:
                layer_input = self.layers[-1].output
                layer_n_in = self.layers[-1].n_out

            if i == len(layers) - 1:
                layer_input = T.max(layer_input, axis=0)
                layer_input = layer_input * self.rng.binomial(n=1, p=hidden_dropout,
                    dtype=theano.config.floatX) / hidden_dropout
            
            xargs = {}

            if layer_type == 'R' and layer == layers[-1]:
                xargs['mask'] = False

            layer = type_map[layer_type](layer_input,
                                         layer_n_in,
                                         layer_size,
                                         **xargs)

            self.layers.append(layer)
        
        self.clean_layers = []
        # Create hidden layers
        for i, layer in enumerate(layers):
            layer_type = layer[0]
            layer_size = layer[1]

            if i == 0:
                layer_input = x
                layer_n_in = n_in
            else:
                layer_input = self.clean_layers[-1].output
                layer_n_in = self.clean_layers[-1].n_out

            if i == len(layers) - 1:
                layer_input = T.max(layer_input, axis=0)

            xargs = {}

            if layer_type == 'R' and layer == layers[-1]:
                xargs['mask'] = False

            layer = type_map[layer_type](layer_input,
                                         layer_n_in,
                                         layer_size,
                                         W=self.layers[i].W,
                                         b=self.layers[i].b,
                                         **xargs)

            self.clean_layers.append(layer)
        
        self._output = theano.function([x], T.argmax(self.clean_layers[-1].output, axis=1))
        
        self.transform = theano.function([x], T.max(self.clean_layers[-2].output, axis=0))
        
        age = T.fmatrix('age')
        dialect = T.lvector('dialect')
        sex = T.lvector('dialect')
        self.age_layer = LinearLayer(self.layers[-1].input,
            self.layers[-1].n_in, 1)
        self.dialect_layer = SoftmaxLayer(self.layers[-1].input,
            self.layers[-1].n_in, 8)
        self.sex_layer = SoftmaxLayer(self.layers[-1].input,
            self.layers[-1].n_in, 2)
        
        loss = -T.mean(T.log(self.layers[-1].output)[T.arange(y.shape[0]), y])
        #loss += ((self.age_layer.output - age)**2).mean()
        loss += -T.mean(T.log(self.dialect_layer.output)[T.arange(
            dialect.shape[0]), dialect])
        loss += -T.mean(T.log(self.sex_layer.output)[T.arange(
            sex.shape[0]), sex])
        
        if l2 != None:
            loss += l2 * sum([(l.W**2).sum() for l in self.layers])
        
        self.trainer = NeuralNetworkTrainer([x, y, dialect, sex], loss,
            self.layers + [self.sex_layer, self.dialect_layer],
            **kwargs)

        
    def train(self, *args):
        return self.trainer.train(*args)


    def output(self, x, batch_size=None):
        if batch_size:
            out = []
            n_batches = int(numpy.ceil(x.shape[0] / float(batch_size)))
            for n in range(n_batches):
                out.append(self._output(x[n * batch_size : (n+1) * batch_size ]))
            return numpy.concatenate(out)
        else:
            return self._output(x)

    def save(self):
        for i, layer in enumerate(self.layers):
            numpy.save("W_%d.npy" % i, layer.W.get_value())
            numpy.save("b_%d.npy" % i, layer.b.get_value())


    def load(self):
        for i, layer in enumerate(self.layers):
            layer.W.set_value(numpy.load("W_%d.npy" % i))
            layer.b.set_value(numpy.load("b_%d.npy" % i))


    def reset(self):
        for layer in self.layers:
            layer.reset()
